"""Ports — the Protocols services depend on. No vendor imports here.

Adapters (app/adapters/*) implement these; the composition root
(app/core/container.py) is the only place that knows which implementation
is wired in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Protocol

from .models import (
    Deck,
    DeckStatus,
    DeckSynthesis,
    QAExtraction,
    Slide,
    SlideNarration,
    SlideStatus,
)


class DeckRepo(Protocol):
    async def create_deck(self, deck: Deck) -> None: ...

    async def get_deck(self, deck_id: str, with_slides: bool = False) -> Optional[Deck]: ...

    async def list_decks(self) -> list[Deck]: ...

    async def delete_deck(self, deck_id: str) -> None: ...

    async def set_deck_status(
        self, deck_id: str, status: DeckStatus, error: Optional[str] = None
    ) -> None: ...

    async def update_deck(
        self,
        deck_id: str,
        *,
        title: Optional[str] = None,
        pdf_path: Optional[str] = None,
        slide_count: Optional[int] = None,
        intro: Optional[str] = None,
        outro: Optional[str] = None,
        persona: Optional[str] = None,
        persona_override: Optional[str] = None,
    ) -> None: ...

    async def ensure_slides(self, deck_id: str, count: int) -> None:
        """Create pending slide rows 1..count if missing (idempotent — resume-safe)."""
        ...

    async def replace_slides(self, deck_id: str, slides: list[Slide]) -> None: ...

    async def get_slides(self, deck_id: str) -> list[Slide]: ...

    async def update_slide(
        self,
        deck_id: str,
        number: int,
        *,
        title: Optional[str] = None,
        bullets: Optional[list[str]] = None,
        notes: Optional[str] = None,
        transition: Optional[str] = None,
        image_path: Optional[str] = None,
        status: Optional[SlideStatus] = None,
        error: Optional[str] = None,
    ) -> None: ...


class SessionLog(Protocol):
    async def start(self, deck_id: str) -> str: ...

    async def finish(self, session_id: str, transcript: list[Any], events: list[Any]) -> None: ...


class BlobStore(Protocol):
    def path_for(self, *parts: str) -> Path: ...

    def dir_for(self, *parts: str) -> Path: ...

    async def save(self, data: bytes, *parts: str) -> Path: ...

    def delete_prefix(self, *parts: str) -> None: ...


class SlideRenderer(Protocol):
    """Sync + CPU/subprocess-bound; callers run it via asyncio.to_thread."""

    @property
    def supports_pptx(self) -> bool: ...

    def convert_to_pdf(self, source: Path, out_dir: Path) -> Path: ...

    def page_count(self, pdf: Path) -> int: ...

    def render_page(self, pdf: Path, index: int, out_path: Path) -> None: ...

    def extract_text(self, pdf: Path, index: int) -> str: ...


class NarrationModel(Protocol):
    async def narrate(
        self,
        *,
        image_png: Optional[bytes],
        page_text: str,
        slide_number: int,
        slide_total: int,
    ) -> SlideNarration: ...

    async def synthesize_deck(self, deck: Deck) -> DeckSynthesis:
        """One pass over all narrated slides -> intro, transitions, outro."""
        ...


class STTFactory(Protocol):
    def create(self) -> Any: ...  # a pipecat STT service


class TTSFactory(Protocol):
    def create(self) -> Any: ...  # a pipecat TTS service


class LLMFactory(Protocol):
    def create(self) -> Any: ...  # a pipecat LLM service


class QAExtractor(Protocol):
    async def extract(self, *, utterance: str, reply: str) -> Optional[QAExtraction]:
        """Turn one raw (audience utterance, presenter reply) pair into a clean
        Q&A log entry, or None if the utterance was not a genuine question."""
        ...
