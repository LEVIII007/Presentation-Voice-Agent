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

from ..domain.models import SlideNarration

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
- Speak about the content, not the artifact: "Revenue grew forty percent to 2.1 million",
  not "This slide shows a chart about revenue".
- No greetings, welcomes, or handoffs to other slides — the presenter adds those live.

LENGTH:
- Scale to the slide: 1-2 sentences for a title, agenda, or section-divider slide; 3-6
  for a typical content slide; up to 8 for a dense chart, table, or diagram. Never pad
  a sparse slide.

Reply with ONLY a JSON object: {"title": "...", "notes": "..."}"""


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
                return await self._complete(vision_content)
            except Exception as e:
                logger.warning(
                    f"Vision narration failed for slide {slide_number} ({e}); "
                    f"falling back to extracted text"
                )

        if not page_text:
            raise RuntimeError("No image narration and no extractable text on the slide")
        return await self._complete(
            prompt + "\n\n(No slide image available — work from the extracted text alone.)"
        )
