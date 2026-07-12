# Environment

Copy `server/.env.example` to `server/.env`.

## Required

- `DEEPGRAM_API_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_CHAT_DEPLOYMENT`
- `TTS_PROVIDER`

## TTS choice

### If `TTS_PROVIDER=cartesia`

- `CARTESIA_API_KEY`

### If `TTS_PROVIDER=azure`

- `AZURE_SPEECH_KEY`
- `AZURE_SPEECH_REGION`
- `AZURE_SPEECH_VOICE`

## Common optional

- `PORT`: backend port
- `DATA_DIR`: runtime storage path
- `DATABASE_URL`: use Postgres instead of SQLite
- `PUBLIC_URL`: needed behind a real domain
- `AZURE_OPENAI_API_VERSION`: override API version if needed
- `SOFFICE_PATH`: explicit LibreOffice path

## Limits and tuning

- `MAX_SESSIONS`
- `MAX_UPLOAD_MB`
- `MAX_SLIDES`
- `NARRATION_CONCURRENCY`
- `LATENCY_LOG`

## Defaults worth knowing

- Default backend port is `7860`.
- Default TTS provider is `cartesia`.
- Empty `DATABASE_URL` means SQLite under `DATA_DIR`.
- Without LibreOffice, `PPTX` uploads are disabled but `PDF` still works.
