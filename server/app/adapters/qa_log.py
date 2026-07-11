"""Q&A log extractor: turns one raw (audience utterance, presenter reply) pair
into a clean question + concise answer for the viewer's question panel — or None
when the utterance was not a genuine question (a greeting, acknowledgment,
navigation/flow command, or speech-to-text garble).

Runs off the voice critical path (the session fires it as a background task), so
a little latency here is fine. Same Azure OpenAI deployment as narration.
"""

from __future__ import annotations

import json
from typing import Optional

from loguru import logger
from openai import AsyncAzureOpenAI

from ..domain.models import QAExtraction

_SYSTEM = """You curate a live "questions asked" log for an audience watching a spoken slide
presentation. You are given ONE exchange: what an audience member said (from speech-to-text,
so it may be mis-transcribed or garbled) and how the presenter replied.

First decide: was the audience member actually ASKING something — a question, or a request to
explain, define, elaborate, give an example, simplify, or justify? Only genuine questions
belong in the log.

NOT genuine (set is_question=false):
- Greetings and pleasantries: "hello", "hi ma'am", "thank you".
- Acknowledgments and backchannel: "yes", "okay", "got it", "right", "continue", "mm-hmm".
- Navigation or flow commands: "next slide", "go back", "skip to slide 8", "pause", "resume",
  "proceed", "move on".
- Pacing/volume feedback: "slow down", "speak louder", "you're too fast".
- Speech-to-text garble that is not a coherent question: "cross your two others right",
  "I can't think you made me".

Genuine (set is_question=true), even if short or telegraphic:
- "Liberty?", "what is the ring?", "why does India rank 24?", "what is the source of this?",
  "explain it to me like a story", "in simpler words", "can you give an example?".

If genuine, also return:
- "question": a clean, concise one-line version of what they asked, in natural question form.
  Fix obvious speech-to-text errors using the presenter's reply as context, but stay faithful
  to their intent — never invent a question they did not ask.
- "answer": a concise, self-contained answer of 1-2 sentences, based ONLY on the presenter's
  reply. The raw reply may be cut off mid-sentence (the audience interrupted); make it read as
  a finished thought, but do NOT add facts, numbers, or claims that are not in the reply. If
  the reply is too garbled or empty to salvage an answer, set is_question=false instead.

Reply with ONLY a JSON object: {"is_question": true, "question": "...", "answer": "..."}.
When is_question is false, "question" and "answer" may be empty strings."""


class AzureQAExtractor:
    def __init__(self, *, api_key: str, endpoint: str, api_version: str, deployment: str):
        self._client = AsyncAzureOpenAI(
            api_key=api_key, azure_endpoint=endpoint, api_version=api_version
        )
        self._deployment = deployment

    async def extract(self, *, utterance: str, reply: str) -> Optional[QAExtraction]:
        utterance = (utterance or "").strip()
        reply = (reply or "").strip()
        if not utterance or not reply:
            return None
        # gpt-5-mini: no custom temperature; leave headroom for reasoning tokens.
        resp = await self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": f'AUDIENCE SAID:\n"{utterance}"\n\nPRESENTER REPLIED:\n"{reply}"',
                },
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
