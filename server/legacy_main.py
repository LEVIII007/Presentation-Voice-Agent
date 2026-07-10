"""FastAPI server for the voice slide presenter.

Endpoints:
- GET  /slides   -> deck content for the frontend to render
- POST /connect  -> RTVI handshake; returns the WebSocket URL to dial
- WS   /ws       -> the live voice session (runs the Pipecat pipeline)
"""

import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

load_dotenv()

from bot import run_bot
from slides import TOPIC, display_slides

app = FastAPI(title="Voice Slide Presenter")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/slides")
async def get_slides():
    """Deck content for the frontend."""
    return {"topic": TOPIC, "slides": display_slides()}


@app.post("/connect")
async def connect():
    """RTVI connect endpoint. Returns the WebSocket URL for the client to dial."""
    host = os.environ.get("PUBLIC_HOST", "localhost")
    port = os.environ.get("PORT", "7860")
    public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if public_url:
        scheme = "wss" if public_url.startswith("https") else "ws"
        host_part = public_url.split("://", 1)[-1]
        ws_url = f"{scheme}://{host_part}/ws"
    else:
        ws_url = f"ws://{host}:{port}/ws"
    logger.info(f"/connect -> {ws_url}")
    return {"ws_url": ws_url}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Live voice session. One pipeline per connection."""
    await websocket.accept()
    logger.info("WebSocket client connected")
    try:
        await run_bot(websocket)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:  # keep one bad session from killing the server
        logger.exception(f"Session error: {e}")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)
