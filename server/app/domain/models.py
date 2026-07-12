"""Domain entities. Pure data — no framework or vendor imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class DeckStatus(str, Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class SlideStatus(str, Enum):
    PENDING = "pending"      # row created, nothing done yet
    RENDERED = "rendered"    # PNG exists, narration pending
    READY = "ready"          # narration written
    FAILED = "failed"


@dataclass
class Slide:
    number: int  # 1-based
    title: str = ""
    bullets: list[str] = field(default_factory=list)
    notes: str = ""
    transition: str = ""  # spoken one-liner bridging from the previous slide
    image_path: Optional[str] = None
    status: SlideStatus = SlideStatus.PENDING
    error: Optional[str] = None


@dataclass
class Deck:
    id: str
    title: str
    source_filename: str
    source_path: Optional[str] = None
    pdf_path: Optional[str] = None
    status: DeckStatus = DeckStatus.UPLOADED
    error: Optional[str] = None
    slide_count: int = 0
    intro: str = ""  # spoken welcome + agenda, generated after narration
    outro: str = ""  # spoken closing summary
    persona: str = ""  # suggested presenter role + tone inferred from the deck
    created_at: Optional[datetime] = None
    slides: list[Slide] = field(default_factory=list)


@dataclass
class SlideNarration:
    """What the narration model produces for one slide."""

    title: str
    notes: str


@dataclass
class DeckSynthesis:
    """Deck-level connective tissue, produced in one pass over all notes."""

    intro: str
    outro: str
    transitions: dict[int, str]  # slide number -> spoken bridge into that slide
    persona: str = ""  # suggested role + tone direction for the live presenter


@dataclass
class QAExtraction:
    """A cleaned audience Q&A exchange for the viewer's question log: a concise
    one-line question and a self-contained answer. The QAExtractor returns None
    instead when the raw utterance was not a genuine question (a greeting,
    acknowledgment, navigation/flow command, or speech-to-text garble)."""

    question: str
    answer: str
