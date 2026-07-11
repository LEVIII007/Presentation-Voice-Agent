# Voice Slides — decks that talk back

Upload a **PDF or PPTX**. It's rendered to slide images, an AI writes speaker
notes for each slide (reading the slide's diagrams and charts, not just its
text), and you get a **talkable deck**: an AI voice presenter that narrates the
slides, jumps to the right slide when you ask a question, and can be interrupted
mid-sentence. Share the link and anyone can talk to your deck in their browser.

This started as a 5-file demo (voice navigation of a hard-coded deck) and is now
a full product: **upload → durable ingestion → review/edit → present/share**.

## Demo

> _Add a 30–60s recording: upload a PDF → watch the slide grid fill in → open
> the viewer → ask "how much does it cost?" → it jumps to the cost slide and
> answers → interrupt it while it's talking._

A built-in **Electric Vehicles 101** deck is seeded on first boot, so the app is
presentable with zero uploads.

## What it does

| Area | What you get |
|---|---|
| **Upload** | Drag-drop PDF/PPTX, client + server validation, byte-level progress. |
| **Ingestion** | PPTX→PDF (LibreOffice) → per-slide PNG (PyMuPDF) → AI narration (Azure gpt-5-mini **vision**, reads charts/diagrams). Durable & resumable. |
| **Progress** | Live thumbnail grid that fills in per slide; **refresh-proof** (server owns all state). Handles failed + retry. |
| **Review** | Creator edits each slide's title + AI narration side-by-side with its image; autosave; low-confidence flags. |
| **Viewer** | Image carousel (no PDF.js), slide rail + manual nav, live captions, starter chips, typed-question fallback, thinking indicator, mic priming, end-screen CTA. Mobile-friendly. |
| **Voice** | Cascaded Deepgram STT → gpt-5-mini (+ `go_to_slide` tool) → Cartesia or Azure Speech TTS, Silero VAD barge-in. |

## Architecture

Ports & adapters (hexagonal) with plain constructor injection. **One process**
runs three planes for the demo; the layering keeps them separable so in
production the worker and voice host become their own processes wired from the
same container.

```
Browser (Vite SPA, framework-free)
  REST  ── upload / status poll / review edits / images ──►  FastAPI  (control plane)
  WS    ── mic audio + slide-sync ─────────────────────────►  Pipecat  (media plane, 1 pipeline/connection)
                                                                 │  Deepgram STT · gpt-5-mini + go_to_slide · Cartesia TTS
  In-process ingestion worker (queue + per-slide checkpoints)  ──┘  (durable plane)

  Postgres/SQLite (decks · slides · sessions)   ·   Blob store (originals + slide PNGs)
```

```
server/app/
├─ core/        settings (pydantic-settings), container.py  ← composition root (only file that knows the vendors)
├─ domain/      entities (Deck, Slide, Session) + ports.py  ← Protocols only
├─ adapters/    deepgram/cartesia/azure factories, narration (vision), renderer (PyMuPDF), repos (SQLAlchemy), blob (local FS)
├─ services/    ingestion.py, worker.py, prompts.py         ← use-cases, vendor-blind
├─ voice/       session.py (pipeline per WS), manager.py    ← connection cap
└─ api/         decks.py, system.py                          ← thin REST routers
```

**Why this shape:** swapping a vendor (the Groq→Azure change we made earlier) or
SQLite→Postgres, or local-disk→S3, is a one-line change in `container.py`, not a
surgery on `bot.py`. Tests inject fakes — a scripted `FakeLLMFactory` can drive
the whole slide-nav loop with no API keys.

### Refresh & crash resilience

The client is **stateless**: `deck_id` lives in the URL and every load just reads
`GET /api/decks/{id}/status`. Refresh mid-ingestion, close the tab, come back
later — ingestion kept running server-side and the page rehydrates. Ingestion
**checkpoints every slide** (rendered PNG on disk, narration in the DB), and on
startup the worker re-enqueues any deck left unfinished — so a server crash
resumes instead of restarting. _(Verified: killed the server mid-ingestion; it
resumed and completed on restart.)_

### Concurrency (10–15 users)

A voice session is I/O-bound (STT/LLM/TTS are remote streams); the only local
CPU is Silero VAD (~3% of a core/session). 15 sessions ≈ half a core. The real
limit at that scale is **vendor quotas**, so there's a hard session cap with
polite rejection (`ConnectionManager`), and ingestion's vision calls are
concurrency-limited to stay under Azure TPM. Nothing CPU-heavy runs in the voice
event loop — rasterization and vision live in the worker (`asyncio.to_thread`).

## Setup

Needs **Deepgram** (STT), **Azure OpenAI** (a `gpt-5-mini` deployment — LLM +
vision narration), and either **Cartesia** or **Azure Speech** for TTS. PPTX also needs **LibreOffice**
(`brew install --cask libreoffice`); without it, PDF uploads still work.

### Backend — Python 3.10–3.13

```bash
cd server
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in keys
python main.py                # serves on http://localhost:7860
```

SQLite DB and slide images are created under `server/data/` automatically.

### Frontend

```bash
cd web
npm install
npm run dev                   # http://localhost:5173
```

Open http://localhost:5173, upload a deck (or open the seeded EV deck), watch it
process, review the narration, then **Present** and talk.

### Docker (one command)

The container builds the Vite app, serves the built frontend from FastAPI, runs
the API + voice WebSocket in the same process, and exposes a single browser port.

1. Copy `server/.env.example` to `server/.env` and fill in your vendor keys.
2. Start everything:

```bash
docker compose up --build
```

Then open http://localhost:5173. Deck data persists in the `voice_slides_data`
Docker volume, and you can change the host port with
`VOICE_SLIDES_PORT=8080 docker compose up --build`.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/decks` | Upload (multipart `file`) → returns `deck_id`; ingestion starts. |
| `GET` | `/api/decks` | List decks. |
| `GET` | `/api/decks/{id}` | Deck + slides (with notes). |
| `GET` | `/api/decks/{id}/status` | Poll target: status + per-slide counts (refresh-proof). |
| `PATCH` | `/api/decks/{id}/slides/{n}` | Edit title/notes. |
| `GET` | `/api/decks/{id}/slides/{n}/image` | Slide PNG. |
| `POST` | `/api/decks/{id}/retry` | Re-run a failed deck. |
| `DELETE` | `/api/decks/{id}` | Delete deck + assets. |
| `POST` | `/connect?deck_id=` | RTVI handshake → `ws_url`. |
| `WS` | `/ws/{deck_id}` | Live voice session. |
| `GET` | `/health` | Vendor keys present, PPTX support, session count. |

## Status / caveats

- **The live audio loop is not verifiable headlessly** (needs a mic + browser
  audio). Everything around it is verified end-to-end: upload, ingestion (real
  Azure vision narration), crash-resume, status polling, review edits, image
  serving, the connect handshake, and the full UI in a real browser. The voice
  pipeline is the proven shape from the original demo, now deck-parameterized;
  the typed-question path uses RTVI's first-class `send-text` so it hits the same
  tool-calling LLM as speech.
- Built against Pipecat `0.0.98`. Chromium-based browser for mic capture.
- Use headphones, or the presenter's own voice can trigger barge-in.
- Original single-file demo preserved as `server/legacy_main.py`
  (`uvicorn legacy_main:app`).

## Measuring voice latency

Every conversational turn is timed by a `LatencyObserver` on the pipeline
([app/voice/latency.py](voice-slides/server/app/voice/latency.py)), so you can see
*where* time goes before optimizing. Each turn logs a breakdown — from the moment
you stop speaking to first audio out:

```
[latency] turn (complete): first_audio=1180.0ms | stt=210.0 llm_ttft=740.0 tts_lead=120.0 speak=1900.0
```

- `first_audio_ms` — the headline: user-stopped-speaking → presenter's first audio.
- `stt_ms` — end of speech → final transcript.
- `llm_ttft_ms` — transcript → first LLM token (this is where reasoning-model think time shows up).
- `tts_lead_ms` — first token → first audio chunk.
- `tool_ms` — `go_to_slide` handler duration, when a slide change happened.

Lines also stream as JSON to `data/latency.jsonl` (plus per-service TTFB from
Pipecat's own metrics). Analyze a run with, e.g.:

```bash
jq -s 'map(select(.record.extra.event=="turn").record.extra.first_audio_ms) | add/length' data/latency.jsonl
```

Toggle with `LATENCY_LOG=false`. The timing math is unit-tested against synthetic
frames ([tests/test_latency.py](voice-slides/server/tests/test_latency.py)); the
*values* only appear on a live mic call.

## Production swaps (where the seams are)

- **Ingestion** in-process worker → Temporal/queue (per-slide checkpoints already
  make activities idempotent).
- **DB** SQLite → Postgres (`DATABASE_URL`).
- **Storage** local FS → S3/GCS (implement the `BlobStore` port; upload via
  presigned URL to skip the app server).
- **Voice + worker** split into their own processes (already isolated planes).
# Presentation-Voice-Agent
