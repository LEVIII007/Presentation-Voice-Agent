"""Health + the RTVI connect handshake."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..domain.models import DeckStatus
from ..services.tts_diagnostics import probe_tts_provider

router = APIRouter(tags=["system"])


def get_container(request: Request):
    return request.app.state.container


@router.get("/health")
async def health(c=Depends(get_container)):
    s = c.settings
    return {
        "ok": True,
        "pptx_supported": c.renderer.supports_pptx,
        "tts_provider": s.tts_provider,
        "sessions": {"active": c.manager.active, "max": c.manager.max_sessions},
        "vendors": {
            "deepgram": bool(s.deepgram_api_key),
            "cartesia": bool(s.cartesia_api_key),
            "azure_speech": bool(s.azure_speech_key and s.azure_speech_region),
            "azure_openai": bool(s.azure_openai_api_key and s.azure_openai_endpoint),
        },
    }


@router.post("/connect")
async def connect(request: Request, deck_id: str = Query(...), c=Depends(get_container)):
    """RTVI connect endpoint. Returns the WebSocket URL for the client to dial."""
    deck = await c.repo.get_deck(deck_id)
    if deck is None:
        raise HTTPException(404, "Deck not found")
    if deck.status != DeckStatus.READY:
        raise HTTPException(409, f"Deck is not ready to present (status: {deck.status.value})")
    if c.manager.active >= c.manager.max_sessions:
        raise HTTPException(503, "This presentation is at capacity — try again shortly")
    tts_probe = await probe_tts_provider(c.settings)
    if not tts_probe.ok:
        raise HTTPException(503, tts_probe.message)

    public_url = c.settings.public_url.rstrip("/")
    if public_url:
        scheme = "wss" if public_url.startswith("https") else "ws"
        host_part = public_url.split("://", 1)[-1]
        ws_url = f"{scheme}://{host_part}/ws/{deck_id}"
    else:
        scheme = "wss" if request.url.scheme == "https" else "ws"
        host = request.headers.get("host") or request.url.netloc or "localhost"
        ws_url = f"{scheme}://{host}/ws/{deck_id}"
    return {"ws_url": ws_url}
