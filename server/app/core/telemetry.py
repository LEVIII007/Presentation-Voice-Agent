"""Structured latency logger.

One reusable sink for timing measurements. Every `event()` produces:
- a human-readable line on the normal console logger (tagged latency=True), and
- a structured JSON line in data/<latency_log_file>, so runs can be analyzed
  offline (e.g. `jq -s 'map(.record.extra)' data/latency.jsonl`).

Kept dependency-free (just loguru, already in use) and safe to construct many
times — the file sink is installed once per process.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

_FILE_SINK_INSTALLED = False


def _install_file_sink(path: Path) -> None:
    global _FILE_SINK_INSTALLED
    if _FILE_SINK_INSTALLED:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        path,
        level="INFO",
        filter=lambda r: r["extra"].get("latency") is True,
        serialize=True,  # one JSON object per line
        enqueue=True,  # async-safe: writes off the caller's thread/event loop
        rotation="20 MB",
    )
    _FILE_SINK_INSTALLED = True


class LatencyLogger:
    """Emit named timing events with arbitrary numeric fields (milliseconds)."""

    def __init__(self, *, enabled: bool = True, file_path: Path | None = None):
        self.enabled = enabled
        if enabled and file_path is not None:
            _install_file_sink(file_path)

    def event(self, name: str, *, session: str | None = None, human: str = "", **fields) -> None:
        if not self.enabled:
            return
        # extra=latency marks the record for the JSONL sink; fields land in record.extra.
        logger.bind(latency=True, event=name, session=session, **fields).info(
            human or f"[latency] {name} {fields}"
        )
