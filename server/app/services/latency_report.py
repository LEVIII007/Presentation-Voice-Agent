"""Build a per-session latency report from the structured JSONL telemetry log."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from ..core.latency_roles import ROLE_META, ROLE_ORDER, infer_latency_role

DEDUPE_WINDOW_SECONDS = 0.1


def _round1(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil((pct / 100) * len(ordered)) - 1))
    return ordered[index]


def _empty_session(session_id: str) -> dict[str, Any]:
    return {
        "session": session_id,
        "started_at": None,
        "ended_at": None,
        "series": {key: [] for key in ROLE_ORDER},
    }


def build_latency_report(path: Path) -> dict[str, Any]:
    """Parse TTFB samples into chart-friendly per-session series."""
    if not path.exists():
        return {
            "exists": False,
            "file": str(path),
            "dedupe_window_ms": int(DEDUPE_WINDOW_SECONDS * 1000),
            "series": [{"key": key, "label": ROLE_META[key]["label"]} for key in ROLE_ORDER],
            "sessions": [],
            "totals": {"sessions": 0, "samples": 0},
        }

    sessions: dict[str, dict[str, Any]] = {}
    last_seen: dict[tuple[str, str, float], float] = {}

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            record = payload.get("record") or {}
            extra = record.get("extra") or {}
            if extra.get("event") != "ttfb":
                continue

            session_id = extra.get("session")
            processor = str(extra.get("processor") or "")
            series_key = extra.get("role") or infer_latency_role(processor)
            if not session_id or series_key not in ROLE_ORDER:
                continue

            ttfb_ms = extra.get("ttfb_ms")
            model = extra.get("model")
            time_info = record.get("time") or {}
            timestamp = time_info.get("timestamp")
            time_repr = time_info.get("repr")
            if ttfb_ms is None or timestamp is None or not time_repr:
                continue

            try:
                ttfb_ms = round(float(ttfb_ms), 1)
                timestamp = float(timestamp)
                at = datetime.fromisoformat(str(time_repr))
            except (TypeError, ValueError):
                continue

            dedupe_key = (session_id, series_key, processor, model, ttfb_ms)
            prev_ts = last_seen.get(dedupe_key)
            if prev_ts is not None and timestamp - prev_ts <= DEDUPE_WINDOW_SECONDS:
                continue
            last_seen[dedupe_key] = timestamp

            session = sessions.setdefault(session_id, _empty_session(session_id))
            if session["started_at"] is None or at < session["started_at"]:
                session["started_at"] = at
            if session["ended_at"] is None or at > session["ended_at"]:
                session["ended_at"] = at
            session["series"][series_key].append(
                {
                    "at": at,
                    "timestamp": timestamp,
                    "ttfb_ms": ttfb_ms,
                    "role": series_key,
                    "processor": processor,
                    "model": model,
                }
            )

    rendered_sessions = []
    total_samples = 0

    for session in sorted(
        sessions.values(),
        key=lambda item: item["started_at"].timestamp() if item["started_at"] is not None else 0,
        reverse=True,
    ):
        started_at = session["started_at"]
        ended_at = session["ended_at"]
        if started_at is None or ended_at is None:
            continue

        session_payload = {
            "session": session["session"],
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "span_s": _round1((ended_at - started_at).total_seconds()),
            "sample_count": 0,
            "series": {},
            "stats": {},
        }

        for key in ROLE_ORDER:
            points = sorted(session["series"][key], key=lambda point: point["timestamp"])
            values = [point["ttfb_ms"] for point in points]
            total_samples += len(points)
            session_payload["sample_count"] += len(points)
            session_payload["stats"][key] = {
                "count": len(points),
                "avg_ms": _round1(mean(values)) if values else None,
                "p95_ms": _round1(_percentile(values, 95)),
                "max_ms": _round1(max(values)) if values else None,
            }
            session_payload["series"][key] = [
                {
                    "sequence": index + 1,
                    "offset_s": _round1((point["at"] - started_at).total_seconds()),
                    "ttfb_ms": point["ttfb_ms"],
                    "role": point["role"],
                    "processor": point["processor"],
                    "model": point["model"],
                    "at": point["at"].isoformat(),
                }
                for index, point in enumerate(points)
            ]

        rendered_sessions.append(session_payload)

    return {
        "exists": True,
        "file": str(path),
        "dedupe_window_ms": int(DEDUPE_WINDOW_SECONDS * 1000),
        "series": [{"key": key, "label": ROLE_META[key]["label"]} for key in ROLE_ORDER],
        "sessions": rendered_sessions,
        "totals": {"sessions": len(rendered_sessions), "samples": total_samples},
    }
