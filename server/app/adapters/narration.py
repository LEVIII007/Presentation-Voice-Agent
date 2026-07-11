"""Narration model: turns a slide image (+ extracted text) into a title and
speaker notes, via Azure OpenAI.

Strategy: vision first (the slide PNG carries diagrams/charts the text layer
misses), text-only fallback if the deployment rejects images or the call
fails. Raises only when both paths are exhausted.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Optional

from loguru import logger
from openai import AsyncAzureOpenAI

from ..domain.models import Deck, DeckSynthesis, SlideNarration

_SYSTEM = """You write speaker notes for an AI voice presenter that will deliver someone
else's slide deck live, out loud, and answer audience questions about it. Your notes are
the presenter's ONLY knowledge of this slide — it cannot see the deck while presenting.
Decks may be corporate, medical, financial, or academic; accuracy matters more than style.

For the slide you are shown, produce:

1. "title": a short slide title, max 8 words. Use the slide's own visible heading when
   there is one; otherwise write a specific descriptive title, never a generic one.

2. "notes": speaker notes in plain spoken sentences, following these rules.

ACCURACY — non-negotiable:
- State only what the slide actually shows. Never invent numbers, names, or claims.
- Capture every specific: numbers with their units, percentages, dates, amounts, names,
  product or drug or course names, versions. If an audience member asks "what was the
  exact figure?", the answer must be in your notes.
- Write the notes so most live audience questions can be answered from them alone, without
  needing to inspect the slide again.
- Use the slide's own key terms verbatim at least once, so questions about those topics
  can be matched back to this slide.
- Expand an abbreviation once if the slide makes its meaning clear; otherwise keep it as
  written. If something is illegible or ambiguous, say what is visible rather than guess.

VISUALS:
- Charts: what is plotted, the axes, the overall trend, and the standout values — peak,
  low, latest, biggest gap.
- Tables: the key rows and figures that carry the message, not a cell-by-cell dump.
- Diagrams and flows: walk through them in order — what connects to what, and what the
  arrows or direction mean.
- Photos and decorative images: one brief mention, only if they carry meaning.

SPOKEN STYLE:
- Plain flowing sentences a person could read aloud. No markdown, bullet symbols, emojis,
  headers, parentheses asides, or stage directions.
- First sentence states the slide's main message; then the supporting details; then why
  it matters, if the slide implies it.
- HARD RULE — speak the content, never describe the artifact. Never write "This slide
  is titled...", "This slide shows...", "The slide lists...", or any opener that treats
  the slide as an object. WRONG: "This slide shows a chart about revenue growth."
  RIGHT: "Revenue grew forty percent to 2.1 million." WRONG: "This slide is the agenda
  and shows two bullet points." RIGHT: "Today we cover two things: heuristics for CSPs,
  and forward checking."
- No greetings, welcomes, or handoffs to other slides — the presenter adds those live.
  Never offer actions to the audience ("If you'd like, I can recap..."); notes carry
  content only.

LENGTH:
- Scale to the slide: 1-2 sentences for a title, agenda, or section-divider slide; 3-6
  for a typical content slide; up to 8 for a dense chart, table, or diagram. Never pad
  a sparse slide.

Reply with ONLY a JSON object: {"title": "...", "notes": "..."}"""


_SYNTHESIS_SYSTEM = """You are preparing an AI voice presenter to deliver a slide deck live.
You are given the full deck: every slide's title and speaker notes, in order. The per-slide
notes are already written; your job is the CONNECTIVE TISSUE that makes it feel like one
coherent talk instead of disconnected pages:

1. "intro": the presenter's opening words, spoken before slide one. 2 to 4 sentences.
   Welcome the audience, say what this presentation is about in plain terms, and preview
   the journey — the 2 or 4 main things the deck covers, in the order they come. Do not
   recite every slide title. If the deck title is a person's name or a filename, do not
   say "welcome to <title>" — describe what the deck actually is (for example a resume,
   a course lecture, a project report).

2. "transitions": for EVERY slide from 2 to the last, one short spoken sentence that
   bridges from the previous slide into this one — why we move here next, or what question
   this slide answers. Max 20 words each. Vary the phrasing; never repeat the same opener
   twice in a row. For a section-divider or thank-you slide, a very short beat is fine
   ("That brings us to the last part.").

3. "outro": the presenter's closing words after the final slide. 1 to 3 sentences: the
   single main takeaway of the whole deck, then invite questions.

4. "persona": one or two sentences directing WHO naturally gives this talk and the tone to
   match, inferred from the deck's subject matter. A healthcare deck → an experienced
   clinician addressing peers; an academic or course deck → a teacher explaining to
   students; a research deck → the researcher; a project, internship, or sales deck → the
   engineer or professional who did the work. Describe the voice, warmth, and vocabulary to
   use (for example "uses clinical terms naturally, calm and reassuring"), NOT a named
   person. This is a private direction to the live presenter and is never spoken aloud.

STYLE — intro, transitions, and outro are spoken aloud by a voice presenter:
- Plain flowing sentences. No markdown, bullets, emojis, or special characters.
- Never mention slide numbers, "slides", "decks", or "sections" mechanically; talk about
  the content ("Now that we know the problem, let's look at the fix", not "Moving to
  slide five").
- Keep the deck's own key terms; do not invent facts not present in the notes.

Reply with ONLY a JSON object:
{"intro": "...", "transitions": {"2": "...", "3": "...", ...}, "outro": "...", "persona": "..."}
The transitions keys are slide numbers as strings, covering every slide from 2 to the last."""


# Notes that open by describing the slide-as-object instead of speaking the
# content — the exact failure mode seen in every early deck.
_ARTIFACT_OPENER = re.compile(r"^\s*(this|the)\s+(slide|page|image|deck)\b", re.IGNORECASE)

_STYLE_CORRECTION = (
    "IMPORTANT: your previous notes opened by describing the slide as an object "
    '("This slide shows..."). Rewrite them to state the content directly, as if '
    "speaking the material itself. Never refer to the slide, page, or image."
)


def _parse(text: str) -> SlideNarration:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"Narration model returned non-JSON: {text[:200]!r}")
        data = json.loads(m.group(0))
    title = str(data.get("title", "")).strip()
    notes = str(data.get("notes", "")).strip()
    if not notes:
        raise ValueError("Narration model returned empty notes")
    return SlideNarration(title=title or "Untitled slide", notes=notes)


class AzureNarrationModel:
    def __init__(self, *, api_key: str, endpoint: str, api_version: str, deployment: str):
        self._client = AsyncAzureOpenAI(
            api_key=api_key, azure_endpoint=endpoint, api_version=api_version
        )
        self._deployment = deployment

    async def _complete(self, user_content) -> SlideNarration:
        # gpt-5-mini: no custom temperature; reasoning tokens need headroom.
        resp = await self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_completion_tokens=2500,
            response_format={"type": "json_object"},
        )
        return _parse(resp.choices[0].message.content or "")

    async def _complete_styled(self, user_content) -> SlideNarration:
        """_complete plus one corrective retry when the notes open with
        artifact-speak; keeps the first (valid) result if the retry fails."""
        narration = await self._complete(user_content)
        if not _ARTIFACT_OPENER.match(narration.notes):
            return narration
        if isinstance(user_content, list):
            retry_content = user_content + [{"type": "text", "text": _STYLE_CORRECTION}]
        else:
            retry_content = f"{user_content}\n\n{_STYLE_CORRECTION}"
        try:
            retry = await self._complete(retry_content)
        except Exception:
            return narration
        return narration if _ARTIFACT_OPENER.match(retry.notes) else retry

    async def narrate(
        self,
        *,
        image_png: Optional[bytes],
        page_text: str,
        slide_number: int,
        slide_total: int,
    ) -> SlideNarration:
        prompt = f"This is slide {slide_number} of {slide_total}."
        if page_text:
            prompt += f"\n\nText extracted from the slide (may be partial):\n{page_text}"

        if image_png:
            b64 = base64.b64encode(image_png).decode()
            vision_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]
            try:
                return await self._complete_styled(vision_content)
            except Exception as e:
                logger.warning(
                    f"Vision narration failed for slide {slide_number} ({e}); "
                    f"falling back to extracted text"
                )

        if not page_text:
            raise RuntimeError("No image narration and no extractable text on the slide")
        return await self._complete_styled(
            prompt + "\n\n(No slide image available — work from the extracted text alone.)"
        )

    async def synthesize_deck(self, deck: Deck) -> DeckSynthesis:
        lines = [f'Deck title: "{deck.title}"', f"Slides: {len(deck.slides)}", ""]
        for s in deck.slides:
            lines.append(f'Slide {s.number} — "{s.title}"')
            lines.append(f"  Notes: {s.notes or '(no notes)'}")
        resp = await self._client.chat.completions.create(
            model=self._deployment,
            messages=[
                {"role": "system", "content": _SYNTHESIS_SYSTEM},
                {"role": "user", "content": "\n".join(lines)},
            ],
            max_completion_tokens=4000,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        intro = str(data.get("intro", "")).strip()
        outro = str(data.get("outro", "")).strip()
        persona = str(data.get("persona", "")).strip()
        if not intro:
            raise ValueError("Synthesis returned an empty intro")
        transitions: dict[int, str] = {}
        for key, value in (data.get("transitions") or {}).items():
            try:
                transitions[int(key)] = str(value).strip()
            except (TypeError, ValueError):
                continue
        return DeckSynthesis(
            intro=intro, outro=outro, transitions=transitions, persona=persona
        )
