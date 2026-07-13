#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Docker Compose is not installed. Install either 'docker compose' or 'docker-compose' and try again." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  DOCKER_CONTEXT="$(docker context show 2>/dev/null || true)"

  if [ "$DOCKER_CONTEXT" = "colima" ] && command -v colima >/dev/null 2>&1; then
    echo "Docker daemon is not running for the 'colima' context. Starting Colima..."
    colima start
  else
    echo "Docker daemon is not running." >&2
    if [ -n "$DOCKER_CONTEXT" ]; then
      echo "Current Docker context: $DOCKER_CONTEXT" >&2
    fi
    if [ -d "/Applications/Docker.app" ]; then
      echo "Start Docker Desktop and run ./start-docker.sh again." >&2
    else
      echo "Start your Docker daemon and run ./start-docker.sh again." >&2
    fi
    exit 1
  fi
fi

if [ "$#" -eq 0 ]; then
  set -- up --build
fi

echo "Starting Voice Slides with: ${COMPOSE_CMD[*]} $*"
exec "${COMPOSE_CMD[@]}" "$@"
