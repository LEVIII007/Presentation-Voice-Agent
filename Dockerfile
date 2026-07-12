# syntax=docker/dockerfile:1

FROM node:20-bookworm-slim AS web-build

WORKDIR /app/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build


FROM python:3.13-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5173 \
    DATA_DIR=/app/runtime-data

WORKDIR /app/server

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    libreoffice \
    libgomp1 \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY server/requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY server/ ./
COPY --from=web-build /app/web/dist /app/web/dist

RUN mkdir -p /app/runtime-data

EXPOSE 5173

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD curl -fsS "http://127.0.0.1:${PORT:-5173}/health" || exit 1

CMD ["python", "main.py"]
