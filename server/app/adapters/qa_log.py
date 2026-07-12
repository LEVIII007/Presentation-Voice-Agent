"""Q&A log extractor: turns one raw (audience utterance, presenter reply) pair
into a clean question + concise answer for the viewer's question panel — or None
when the utterance was not a genuine question (a greeting, acknowledgment,
navigation/flow command, or speech-to-text garble).

Runs off the voice critical path (the session fires it as a background task), so
a little latency here is fine. Same Azure OpenAI deployment as narration.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

try:
    from loguru import logger
except ImportError:  # pragma: no cover - fallback for lightweight test environments
    logger = logging.getLogger(__name__)

from ..domain.models import QAExtraction

_SYSTEM = """You curate a live "questions the audience asked" log for people watching a spoken
slide presentation. You are given ONE exchange — what an audience member said (from
speech-to-text, so it may be mis-transcribed or garbled) and how the presenter replied — plus
CONTEXT: what the talk is about, which slide they asked from, which slide answered it, and the
recent conversation.

Decide ONE thing: was this a genuine question or request about the PRESENTATION'S SUBJECT
MATTER — the content on the slides — worth keeping in a log the viewer will revisit later?
Only those belong in the log. Use the context to judge intent; the same words can be a real
question or just noise depending on what was happening.

Set is_question=false for anything that is NOT a content question, including:
- Greetings and pleasantries, even repeated: "hello", "hello hello hello", "hi ma'am", "thanks".
- Acknowledgments and backchannel: "yes", "okay", "got it", "right", "sure", "mm-hmm".
- Navigation or flow commands: "next slide", "go back", "skip to slide 8", "pause", "resume",
  "proceed", "continue", "move on".
- Pacing/volume feedback: "slow down", "speak louder", "you're too fast".
- META or APP-DIRECTED utterances — reactions to the presenter stopping, stalling, or to a
  technical glitch, NOT questions about the content: "what happened?", "are you there?",
  "can you hear me?", "why did you stop?", "is it working?", "did you freeze?", "hello, are you
  there?". If the recent conversation shows the presenter had just gone quiet or was
  interrupted, treat a puzzled "what happened / where were we / you stopped" as one of these.
- Speech-to-text garble that is not a coherent question: "cross your two others right",
  "I can't think you made me".

Set is_question=true only for a real question or a request to explain, define, elaborate,
give an example, simplify, or justify SOMETHING IN THE TALK — even if short or telegraphic:
"Liberty?", "what is the ring?", "why does India rank 24?", "what is the source of this?",
"explain it like a story", "in simpler words", "can you give an example?".

If genuine, also return:
- "question": a clean, concise one-line version of what they asked, in natural question form.
  It MUST stand on its own a week later, without the surrounding transcript. Fix obvious
  speech-to-text errors using the reply and context, but stay faithful to their intent —
  never invent a question they did not ask.
- Resolve vague references like "this", "that", "it", "one", "here", "direct example", or
  "more detail" ONLY when the context makes the referent clear. Use the nearest reliable
  anchor in this order: recent presenter explanation, asked-from slide topic, answered-from
  slide topic, then presentation title.
- If that referent is STILL ambiguous after using the context, set is_question=false. Do not
  guess. Do not broaden the question to a generic deck topic just to keep it.
- "answer": a concise, self-contained answer of 1-2 sentences, based ONLY on the presenter's
  reply. The raw reply may be cut off mid-sentence (the audience interrupted); make it read as
  a finished thought, but do NOT add facts, numbers, or claims that are not in the reply. If
  the reply is too garbled or empty to salvage an answer, set is_question=false instead.

Reply with ONLY a JSON object: {"is_question": true, "question": "...", "answer": "..."}.
When is_question is false, "question" and "answer" may be empty strings."""


def _truncate(text: str, limit: int = 300) -> str:
    text = str(text or "").strip().replace("\n", " ")
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _format_slide_ref(prefix: str, number: int | None, title: str) -> str | None:
    bits: list[str] = []
    if number is not None:
        bits.append(f"Slide {number}")
    title = str(title or "").strip()
    if title:
        bits.append(f'"{title}"')
    if not bits:
        return None
    return f"{prefix}: {' — '.join(bits)}"


def _format_context(
    deck_title: str,
    ask_slide_number: int | None,
    ask_slide_title: str,
    answer_slide_number: int | None,
    answer_slide_title: str,
    history: Optional[list[dict]],
) -> str:
    lines: list[str] = []
    if deck_title:
        lines.append(f'PRESENTATION: "{deck_title}"')
    ask_slide = _format_slide_ref("ASKED ON", ask_slide_number, ask_slide_title)
    if ask_slide:
        lines.append(ask_slide)
    answer_slide = _format_slide_ref("ANSWERED ON", answer_slide_number, answer_slide_title)
    if answer_slide:
        lines.append(answer_slide)
    turns = [t for t in (history or []) if isinstance(t, dict) and str(t.get("content", "")).strip()]
    if turns:
        lines.append("RECENT CONVERSATION (oldest first, context only):")
        for t in turns:
            role = "Presenter" if t.get("role") == "assistant" else "Audience"
            content = _truncate(str(t.get("content", "")))
            slide = t.get("slide")
            if isinstance(slide, int):
                lines.append(f"- {role} (slide {slide}): {content}")
            else:
                lines.append(f"- {role}: {content}")
    return "\n".join(lines)


def _build_user_content(
    *,
    utterance: str,
    reply: str,
    deck_title: str = "",
    ask_slide_number: int | None = None,
    ask_slide_title: str = "",
    answer_slide_number: int | None = None,
    answer_slide_title: str = "",
    history: Optional[list[dict]] = None,
) -> str:
    context = _format_context(
        deck_title,
        ask_slide_number,
        ask_slide_title,
        answer_slide_number,
        answer_slide_title,
        history,
    )
    return (
        (f"{context}\n\n" if context else "")
        + f'AUDIENCE SAID:\n"{utterance}"\n\nPRESENTER REPLIED:\n"{reply}"'
    )


class AzureQAExtractor:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        api_version: str,
        deployment: str,
        client: Any | None = None,
    ):
        if client is None:
            from openai import AsyncAzureOpenAI

            client = AsyncAzureOpenAI(
                api_key=api_key, azure_endpoint=endpoint, api_version=api_version
            )
        self._client = client
        self._deployment = deployment

    async def extract(
        self,
        *,
        utterance: str,
        reply: str,
        deck_title: str = "",
        ask_slide_number: int | None = None,
        ask_slide_title: str = "",
        answer_slide_number: int | None = None,
        answer_slide_title: str = "",
        history: Optional[list[dict]] = None,
    ) -> Optional[QAExtraction]:
        utterance = (utterance or "").strip()
        reply = (reply or "").strip()
        if not utterance or not reply:
            return None
        user_content = _build_user_content(
            utterance=utterance,
            reply=reply,
            deck_title=deck_title,
            ask_slide_number=ask_slide_number,
            ask_slide_title=ask_slide_title,
            answer_slide_number=answer_slide_number,
            answer_slide_title=answer_slide_title,
            history=history,
        )
        # gpt-5-mini: no custom temperature; leave headroom for reasoning tokens.
        resp = await self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_completion_tokens=1500,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Q&A extractor returned non-JSON: {raw[:200]!r}")
            return None
        if not data.get("is_question"):
            return None
        question = str(data.get("question", "")).strip()
        answer = str(data.get("answer", "")).strip()
        if not question or not answer:
            return None
        return QAExtraction(question=question, answer=answer)
