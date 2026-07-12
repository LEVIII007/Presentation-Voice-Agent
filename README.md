# Voice Slides

Turn a PDF or PPTX into a voice-presentable deck.

## What it does

- Upload a `PDF` or `PPTX`.
- Convert the deck into slide images.
- Generate slide narration with Azure OpenAI vision.
- Let the creator review and edit the generated notes.
- Present the deck in the browser with voice Q&A.
- Jump to the right slide when the user asks about a topic.

## Product flow

1. Upload a deck.
2. Backend renders slides and writes narration.
3. User reviews slide notes.
4. Viewer opens a shareable presentation.
5. Voice session answers questions and moves between slides.

## Stack

- Frontend: Vite + vanilla JavaScript
- Backend: FastAPI + Pipecat
- Storage: SQLite + local files by default
- AI vendors: Deepgram, Azure OpenAI, Cartesia or Azure Speech

## Main folders

- `web/`: browser app
- `server/app/api/`: REST endpoints
- `server/app/services/`: ingestion and background work
- `server/app/voice/`: live voice session handling
- `server/app/adapters/`: vendor and storage integrations

## Notes

- A demo deck is seeded on first boot.
- `PPTX` upload needs LibreOffice.
- `PDF` upload works without LibreOffice.
- Docker serves frontend and backend from one container.

## Docs

- [setup.md](setup.md)
- [env.md](env.md)
