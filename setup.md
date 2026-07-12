# Setup

## Requirements

- Python `3.10+`
- Node `20+`
- `npm`
- LibreOffice for `PPTX` upload support
- Vendor keys from `env.md`

## Local run

### Backend

1. Go to `server/`.
2. Create and activate a virtual environment.
3. Install `requirements.txt`.
4. Copy `.env.example` to `.env`.
5. Start with `python main.py`.
6. Backend runs on `http://localhost:7860` by default.

### Frontend

1. Go to `web/`.
2. Run `npm install`.
3. Start with `npm run dev`.
4. Frontend runs on `http://localhost:5173`.

## Docker run

1. Fill `server/.env`.
2. Run `docker compose up --build`.
3. Open `http://localhost:5173`.

## Data

- Local data is stored in `server/data/`.
- Docker data is stored in the `voice_slides_data` volume.
- A sample deck is available on first boot.
