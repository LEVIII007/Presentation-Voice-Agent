"""SQL implementations of the DeckRepo and SessionLog ports.

Each method opens its own short-lived session, so the repo is safe to share
between the API handlers, the ingestion worker, and voice sessions.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.exc import IntegrityError

from ..domain.models import Deck, DeckStatus, Slide, SlideStatus
from .db import DeckRow, SessionRow, SlideRow


def _deck_from_row(row: DeckRow) -> Deck:
    return Deck(
        id=row.id,
        title=row.title,
        source_filename=row.source_filename,
        source_path=row.source_path,
        pdf_path=row.pdf_path,
        status=DeckStatus(row.status),
        error=row.error,
        slide_count=row.slide_count,
        intro=row.intro or "",
        outro=row.outro or "",
        persona=row.persona or "",
        created_at=row.created_at,
    )


def _slide_from_row(row: SlideRow) -> Slide:
    return Slide(
        number=row.number,
        title=row.title,
        bullets=json.loads(row.bullets or "[]"),
        notes=row.notes,
        transition=row.transition or "",
        image_path=row.image_path,
        status=SlideStatus(row.status),
        error=row.error,
    )


class SqlDeckRepo:
    def __init__(self, session_factory: async_sessionmaker):
        self._sf = session_factory

    async def create_deck(self, deck: Deck) -> None:
        async with self._sf() as s:
            s.add(
                DeckRow(
                    id=deck.id,
                    title=deck.title,
                    source_filename=deck.source_filename,
                    source_path=deck.source_path,
                    pdf_path=deck.pdf_path,
                    status=deck.status.value,
                    slide_count=deck.slide_count,
                    persona=deck.persona,
                )
            )
            await s.commit()

    async def get_deck(self, deck_id: str, with_slides: bool = False) -> Optional[Deck]:
        async with self._sf() as s:
            row = await s.get(DeckRow, deck_id)
            if row is None:
                return None
            deck = _deck_from_row(row)
        if with_slides:
            deck.slides = await self.get_slides(deck_id)
        return deck

    async def list_decks(self) -> list[Deck]:
        async with self._sf() as s:
            rows = (
                await s.execute(select(DeckRow).order_by(DeckRow.created_at.desc()))
            ).scalars().all()
            return [_deck_from_row(r) for r in rows]

    async def delete_deck(self, deck_id: str) -> None:
        async with self._sf() as s:
            await s.execute(delete(SlideRow).where(SlideRow.deck_id == deck_id))
            await s.execute(delete(DeckRow).where(DeckRow.id == deck_id))
            await s.commit()

    async def set_deck_status(
        self, deck_id: str, status: DeckStatus, error: Optional[str] = None
    ) -> None:
        async with self._sf() as s:
            await s.execute(
                update(DeckRow)
                .where(DeckRow.id == deck_id)
                .values(status=status.value, error=error)
            )
            await s.commit()

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
    ) -> None:
        values: dict[str, Any] = {}
        if title is not None:
            values["title"] = title
        if pdf_path is not None:
            values["pdf_path"] = pdf_path
        if slide_count is not None:
            values["slide_count"] = slide_count
        if intro is not None:
            values["intro"] = intro
        if outro is not None:
            values["outro"] = outro
        if persona is not None:
            values["persona"] = persona
        if not values:
            return
        async with self._sf() as s:
            await s.execute(update(DeckRow).where(DeckRow.id == deck_id).values(**values))
            await s.commit()

    async def ensure_slides(self, deck_id: str, count: int) -> None:
        async with self._sf() as s:
            existing = set(
                (
                    await s.execute(
                        select(SlideRow.number).where(SlideRow.deck_id == deck_id)
                    )
                ).scalars()
            )
            for n in range(1, count + 1):
                if n not in existing:
                    s.add(SlideRow(deck_id=deck_id, number=n, status=SlideStatus.PENDING.value))
            try:
                await s.commit()
            except IntegrityError:
                # Concurrent creation of the same rows — fine, they exist now.
                await s.rollback()

    async def replace_slides(self, deck_id: str, slides: list[Slide]) -> None:
        async with self._sf() as s:
            await s.execute(delete(SlideRow).where(SlideRow.deck_id == deck_id))
            for sl in slides:
                s.add(
                    SlideRow(
                        deck_id=deck_id,
                        number=sl.number,
                        title=sl.title,
                        bullets=json.dumps(sl.bullets),
                        notes=sl.notes,
                        transition=sl.transition,
                        image_path=sl.image_path,
                        status=sl.status.value,
                        error=sl.error,
                    )
                )
            await s.commit()

    async def get_slides(self, deck_id: str) -> list[Slide]:
        async with self._sf() as s:
            rows = (
                await s.execute(
                    select(SlideRow)
                    .where(SlideRow.deck_id == deck_id)
                    .order_by(SlideRow.number)
                )
            ).scalars().all()
            return [_slide_from_row(r) for r in rows]

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
    ) -> None:
        values: dict[str, Any] = {}
        if title is not None:
            values["title"] = title
        if bullets is not None:
            values["bullets"] = json.dumps(bullets)
        if notes is not None:
            values["notes"] = notes
        if transition is not None:
            values["transition"] = transition
        if image_path is not None:
            values["image_path"] = image_path
        if status is not None:
            values["status"] = status.value
            values["error"] = error  # clear or set alongside status changes
        elif error is not None:
            values["error"] = error
        if not values:
            return
        async with self._sf() as s:
            await s.execute(
                update(SlideRow)
                .where(SlideRow.deck_id == deck_id, SlideRow.number == number)
                .values(**values)
            )
            await s.commit()


class SqlSessionLog:
    def __init__(self, session_factory: async_sessionmaker):
        self._sf = session_factory

    async def start(self, deck_id: str) -> str:
        session_id = uuid.uuid4().hex[:12]
        async with self._sf() as s:
            s.add(SessionRow(id=session_id, deck_id=deck_id))
            await s.commit()
        return session_id

    async def finish(self, session_id: str, transcript: list[Any], events: list[Any]) -> None:
        async with self._sf() as s:
            await s.execute(
                update(SessionRow)
                .where(SessionRow.id == session_id)
                .values(
                    ended_at=datetime.now(timezone.utc),
                    transcript=json.dumps(transcript, default=str),
                    events=json.dumps(events, default=str),
                )
            )
            await s.commit()
