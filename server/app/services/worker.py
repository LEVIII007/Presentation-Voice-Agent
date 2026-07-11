"""In-process ingestion worker.

A single asyncio consumer drains a queue of deck ids; on startup it re-enqueues
any deck left in `uploaded`/`processing` (i.e. the server died mid-ingestion),
which — combined with the per-slide checkpoints in IngestionService — gives
crash/restart resumability without external infra. The production swap for
this seam is a real queue + durable workflow engine (e.g. Temporal).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from ..domain.models import DeckStatus
from ..domain.ports import DeckRepo
from .ingestion import IngestionService


class IngestionWorker:
    def __init__(self, ingestion: IngestionService, repo: DeckRepo):
        self._ingestion = ingestion
        self._repo = repo
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        # Crash recovery: anything not finished gets re-run (idempotent).
        for deck in await self._repo.list_decks():
            if deck.status in (DeckStatus.UPLOADED, DeckStatus.PROCESSING):
                logger.info(f"Recovering unfinished deck {deck.id} ({deck.status})")
                self.enqueue(deck.id)
            elif deck.status == DeckStatus.READY and not deck.intro:
                # Decks narrated before the synthesis stage existed: backfill
                # their intro/transitions (ingest() short-circuits to that).
                logger.info(f"Backfilling synthesis for deck {deck.id}")
                self.enqueue(deck.id)
        self._task = asyncio.create_task(self._run(), name="ingestion-worker")

    def enqueue(self, deck_id: str) -> None:
        self._queue.put_nowait(deck_id)

    async def _run(self) -> None:
        while True:
            deck_id = await self._queue.get()
            try:
                await self._ingestion.ingest(deck_id)
            except Exception:
                logger.exception(f"Worker crashed on deck {deck_id}")
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
