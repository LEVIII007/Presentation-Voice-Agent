"""Deck REST API: upload, list, status polling, review edits, images."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..domain.models import Deck, DeckStatus, Slide, SlideStatus

router = APIRouter(prefix="/api/decks", tags=["decks"])

_CHUNK = 1024 * 1024


def get_container(request: Request):
    return request.app.state.container


def _deck_dict(deck: Deck) -> dict:
    return {
        "id": deck.id,
        "title": deck.title,
        "source_filename": deck.source_filename,
        "status": deck.status.value,
        "error": deck.error,
        "slide_count": deck.slide_count,
        "intro": deck.intro,
        "outro": deck.outro,
        "created_at": deck.created_at.isoformat() if deck.created_at else None,
    }


def _slide_dict(s: Slide) -> dict:
    return {
        "number": s.number,
        "title": s.title,
        "bullets": s.bullets,
        "notes": s.notes,
        "transition": s.transition,
        "status": s.status.value,
        "error": s.error,
        "has_image": bool(s.image_path),
    }


@router.post("", status_code=201)
async def upload_deck(file: UploadFile, c=Depends(get_container)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".pdf", ".pptx"):
        raise HTTPException(400, "Only .pdf and .pptx files are supported")
    if ext == ".pptx" and not c.renderer.supports_pptx:
        raise HTTPException(
            400,
            "PPTX conversion needs LibreOffice, which isn't installed on this "
            "server. Export your deck to PDF and upload that instead.",
        )

    max_bytes = c.settings.max_upload_mb * 1024 * 1024
    data = bytearray()
    while chunk := await file.read(_CHUNK):
        data.extend(chunk)
        if len(data) > max_bytes:
            raise HTTPException(413, f"File exceeds the {c.settings.max_upload_mb} MB limit")
    if not data:
        raise HTTPException(400, "Empty file")

    deck_id = uuid.uuid4().hex[:12]
    path = await c.blobs.save(bytes(data), "decks", deck_id, f"source{ext}")
    deck = Deck(
        id=deck_id,
        title=Path(file.filename).stem,
        source_filename=file.filename,
        source_path=str(path),
        status=DeckStatus.UPLOADED,
    )
    await c.repo.create_deck(deck)
    c.worker.enqueue(deck_id)
    return _deck_dict(deck)


@router.get("")
async def list_decks(c=Depends(get_container)):
    return {"decks": [_deck_dict(d) for d in await c.repo.list_decks()]}


@router.get("/{deck_id}")
async def get_deck(deck_id: str, c=Depends(get_container)):
    deck = await c.repo.get_deck(deck_id, with_slides=True)
    if deck is None:
        raise HTTPException(404, "Deck not found")
    out = _deck_dict(deck)
    out["slides"] = [_slide_dict(s) for s in deck.slides]
    return out


@router.get("/{deck_id}/status")
async def deck_status(deck_id: str, c=Depends(get_container)):
    """Poll target for the processing screen. The client is stateless: it
    renders whatever this returns, so a page refresh mid-ingestion costs
    nothing."""
    deck = await c.repo.get_deck(deck_id)
    if deck is None:
        raise HTTPException(404, "Deck not found")
    slides = await c.repo.get_slides(deck_id)
    return {
        "status": deck.status.value,
        "error": deck.error,
        "slide_count": deck.slide_count or len(slides),
        "counts": {
            "total": len(slides),
            "rendered": sum(1 for s in slides if s.image_path),
            "narrated": sum(1 for s in slides if s.status == SlideStatus.READY),
            "failed": sum(1 for s in slides if s.status == SlideStatus.FAILED),
        },
        "slides": [
            {"number": s.number, "status": s.status.value, "has_image": bool(s.image_path)}
            for s in slides
        ],
    }


class SlidePatch(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None


@router.patch("/{deck_id}/slides/{number}")
async def patch_slide(deck_id: str, number: int, patch: SlidePatch, c=Depends(get_container)):
    deck = await c.repo.get_deck(deck_id)
    if deck is None:
        raise HTTPException(404, "Deck not found")
    await c.repo.update_slide(deck_id, number, title=patch.title, notes=patch.notes)
    return {"ok": True}


@router.get("/{deck_id}/slides/{number}/image")
async def slide_image(deck_id: str, number: int, c=Depends(get_container)):
    slides = await c.repo.get_slides(deck_id)
    match = next((s for s in slides if s.number == number), None)
    if match is None or not match.image_path or not Path(match.image_path).exists():
        raise HTTPException(404, "No image for this slide")
    return FileResponse(
        match.image_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/{deck_id}/retry")
async def retry_deck(deck_id: str, c=Depends(get_container)):
    deck = await c.repo.get_deck(deck_id)
    if deck is None:
        raise HTTPException(404, "Deck not found")
    if deck.status != DeckStatus.FAILED:
        raise HTTPException(409, "Deck is not in a failed state")
    for s in await c.repo.get_slides(deck_id):
        if s.status == SlideStatus.FAILED:
            await c.repo.update_slide(deck_id, s.number, status=SlideStatus.PENDING)
    await c.repo.set_deck_status(deck_id, DeckStatus.UPLOADED)
    c.worker.enqueue(deck_id)
    return {"ok": True}


@router.delete("/{deck_id}", status_code=204)
async def delete_deck(deck_id: str, c=Depends(get_container)):
    deck = await c.repo.get_deck(deck_id)
    if deck is None:
        raise HTTPException(404, "Deck not found")
    if deck.status == DeckStatus.PROCESSING:
        raise HTTPException(409, "Deck is still processing; wait for it to finish")
    await c.repo.delete_deck(deck_id)
    c.blobs.delete_prefix("decks", deck_id)
