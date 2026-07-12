"""App assembly: builds the container in lifespan, mounts REST routers and the
per-deck voice WebSocket.

Single process runs all three planes (API, voice, ingestion worker) for the
demo; the layering keeps them separable — in prod the worker and the voice
host become their own processes wired from this same container.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

load_dotenv()

from .api import decks, latency, system
from .bootstrap_data import merge_bundled_data, prime_runtime_data
from .core.container import build_container
from .core.settings import Settings
from .seed import seed_demo_deck


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    bundled_data_dir = Path(__file__).resolve().parents[1] / "data"
    prime_runtime_data(settings.data_dir, bundled_data_dir)
    container = await build_container(settings)
    app.state.container = container
    merge_bundled_data(settings.data_dir, bundled_data_dir)
    await seed_demo_deck(container.repo)
    await container.worker.start()
    logger.info(
        f"Ready: db={settings.resolved_database_url} data={settings.data_dir} "
        f"pptx={'yes' if container.renderer.supports_pptx else 'no (PDF only)'}"
    )
    yield
    await container.worker.stop()
    await container.engine.dispose()


app = FastAPI(title="Voice Slide Presenter", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(latency.router)
app.include_router(decks.router)


@app.websocket("/ws/{deck_id}")
async def websocket_endpoint(websocket: WebSocket, deck_id: str):
    """Live voice session. One pipeline per connection."""
    await websocket.accept()
    logger.info(f"WebSocket client connected for deck {deck_id}")
    try:
        await websocket.app.state.container.session_runner.run(websocket, deck_id)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:  # keep one bad session from killing the server
        logger.exception(f"Session error: {e}")


frontend_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
else:
    logger.warning(f"Frontend dist not found at {frontend_dist}; serving API only")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)
