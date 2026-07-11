"""Ingestion pipeline: uploaded file -> per-slide PNGs -> AI narration.

Durable by checkpointing every step into slide rows: if the process dies and
the worker re-runs a deck, slides already READY are skipped and rendering is
skipped when the PNG is already on disk. Refreshing the browser never matters —
all state lives here, server-side.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from ..core.settings import Settings
from ..domain.models import Deck, DeckStatus, Slide, SlideStatus
from ..domain.ports import BlobStore, DeckRepo, NarrationModel, SlideRenderer


class IngestionService:
    def __init__(
        self,
        repo: DeckRepo,
        blobs: BlobStore,
        renderer: SlideRenderer,
        narrator: NarrationModel,
        settings: Settings,
    ):
        self._repo = repo
        self._blobs = blobs
        self._renderer = renderer
        self._narrator = narrator
        self._settings = settings

    async def ingest(self, deck_id: str) -> None:
        deck = await self._repo.get_deck(deck_id)
        if deck is None:
            logger.warning(f"ingest: deck {deck_id} not found")
            return
        if deck.status == DeckStatus.READY:
            # Backfill: decks narrated before the synthesis stage (or before the
            # persona field) existed get their intro/transitions/outro/persona
            # without a full re-ingest.
            if not deck.intro or not deck.persona:
                await self._synthesize(deck_id)
            return
        try:
            await self._run(deck)
        except Exception as e:
            logger.exception(f"Ingestion failed for deck {deck_id}")
            await self._repo.set_deck_status(deck_id, DeckStatus.FAILED, error=str(e))

    async def _run(self, deck: Deck) -> None:
        await self._repo.set_deck_status(deck.id, DeckStatus.PROCESSING)

        # Stage 1: make sure we have a PDF (PPTX goes through LibreOffice).
        pdf_path = Path(deck.pdf_path) if deck.pdf_path else None
        if pdf_path is None or not pdf_path.exists():
            source = Path(deck.source_path)
            if source.suffix.lower() == ".pdf":
                pdf_path = source
            else:
                out_dir = self._blobs.dir_for("decks", deck.id, "converted")
                pdf_path = await asyncio.to_thread(
                    self._renderer.convert_to_pdf, source, out_dir
                )
            await self._repo.update_deck(deck.id, pdf_path=str(pdf_path))

        count = await asyncio.to_thread(self._renderer.page_count, pdf_path)
        if count == 0:
            raise ValueError("The file contains no pages")
        if count > self._settings.max_slides:
            raise ValueError(
                f"Deck has {count} slides; the limit is {self._settings.max_slides}"
            )
        await self._repo.update_deck(deck.id, slide_count=count)
        await self._repo.ensure_slides(deck.id, count)  # idempotent — resume-safe

        # Stage 2: render + narrate each slide. Narration concurrency is capped
        # to stay under Azure TPM limits; already-READY slides are skipped.
        slides = await self._repo.get_slides(deck.id)
        sem = asyncio.Semaphore(self._settings.narration_concurrency)
        await asyncio.gather(
            *[
                self._process_slide(deck.id, pdf_path, s, count, sem)
                for s in slides
                if s.status != SlideStatus.READY
            ]
        )

        # Stage 3: finalize.
        slides = await self._repo.get_slides(deck.id)
        failed = [s for s in slides if s.status == SlideStatus.FAILED]
        if len(failed) > len(slides) // 2:
            raise RuntimeError(
                f"Narration failed on {len(failed)} of {len(slides)} slides"
            )
        first = slides[0] if slides else None
        if first and first.title and first.status == SlideStatus.READY:
            await self._repo.update_deck(deck.id, title=first.title)

        # Stage 4: deck-level synthesis — intro, per-slide transitions, outro.
        # Best-effort: a deck without connective tissue is still presentable.
        await self._synthesize(deck.id)

        await self._repo.set_deck_status(deck.id, DeckStatus.READY)
        logger.info(f"Deck {deck.id} ready ({len(slides)} slides, {len(failed)} degraded)")

    async def _synthesize(self, deck_id: str) -> None:
        deck = await self._repo.get_deck(deck_id, with_slides=True)
        if deck is None or not deck.slides:
            return
        try:
            synthesis = await self._narrator.synthesize_deck(deck)
        except Exception:
            logger.exception(f"Deck synthesis failed for {deck_id}; presenting without it")
            return
        await self._repo.update_deck(
            deck_id,
            intro=synthesis.intro,
            outro=synthesis.outro,
            persona=synthesis.persona,
        )
        for n, text in synthesis.transitions.items():
            if text and 1 < n <= len(deck.slides):
                await self._repo.update_slide(deck_id, n, transition=text)
        logger.info(
            f"Deck {deck_id} synthesized: intro + {len(synthesis.transitions)} transitions"
        )

    async def _process_slide(
        self,
        deck_id: str,
        pdf_path: Path,
        slide: Slide,
        total: int,
        sem: asyncio.Semaphore,
    ) -> None:
        n = slide.number
        try:
            # Render (checkpoint 1) — skip if the PNG survived a previous run.
            image_path = Path(slide.image_path) if slide.image_path else None
            if image_path is None or not image_path.exists():
                image_path = self._blobs.path_for("decks", deck_id, "slides", f"{n:03d}.png")
                await asyncio.to_thread(
                    self._renderer.render_page, pdf_path, n - 1, image_path
                )
                await self._repo.update_slide(
                    deck_id, n, image_path=str(image_path), status=SlideStatus.RENDERED
                )

            # Narrate (checkpoint 2).
            page_text = await asyncio.to_thread(self._renderer.extract_text, pdf_path, n - 1)
            image_png = await asyncio.to_thread(image_path.read_bytes)
            async with sem:
                narration = await self._narrator.narrate(
                    image_png=image_png,
                    page_text=page_text,
                    slide_number=n,
                    slide_total=total,
                )
            await self._repo.update_slide(
                deck_id,
                n,
                title=narration.title,
                notes=narration.notes,
                status=SlideStatus.READY,
            )
        except Exception as e:
            logger.exception(f"Slide {n} of deck {deck_id} failed")
            await self._repo.update_slide(
                deck_id, n, status=SlideStatus.FAILED, error=str(e)
            )
