import json
from datetime import datetime, timezone

from app.services.latency_report import build_latency_report


def _line(*, event, session, processor, ttfb_ms, ts, role=None, model=None):
    return json.dumps(
        {
            "record": {
                "extra": {
                    "latency": True,
                    "event": event,
                    "session": session,
                    "role": role,
                    "processor": processor,
                    "model": model,
                    "ttfb_ms": ttfb_ms,
                },
                "time": {
                    "repr": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "timestamp": ts,
                },
            }
        }
    )


def test_latency_report_groups_sessions_and_dedupes(tmp_path):
    log_path = tmp_path / "latency.jsonl"
    log_path.write_text(
        "\n".join(
            [
                _line(event="ttfb", session="sess-a", processor="DeepgramSTTService#0", ttfb_ms=120.0, ts=1000.0),
                _line(event="ttfb", session="sess-a", processor="DeepgramSTTService#0", ttfb_ms=120.0, ts=1000.03),
                _line(event="ttfb", session="sess-a", processor="AzureLLMService#0", ttfb_ms=2400.0, ts=1001.0, role="llm", model="gpt-5-mini"),
                _line(event="ttfb", session="sess-a", processor="AzureV2TTSService#0", ttfb_ms=140.0, ts=1001.4, role="tts", model="en-US-Ava"),
                _line(event="turn", session="sess-a", processor="ignored", ttfb_ms=0, ts=1002.0),
                _line(event="ttfb", session="sess-b", processor="AzureLLMService#1", ttfb_ms=1800.0, ts=2000.0, role="llm"),
            ]
        ),
        encoding="utf-8",
    )

    report = build_latency_report(log_path)

    assert report["exists"] is True
    assert report["totals"] == {"sessions": 2, "samples": 4}

    newest, older = report["sessions"]
    assert newest["session"] == "sess-b"
    assert newest["stats"]["llm"]["count"] == 1
    assert newest["stats"]["llm"]["avg_ms"] == 1800.0
    assert newest["series"]["stt"] == []

    assert older["session"] == "sess-a"
    assert [point["ttfb_ms"] for point in older["series"]["stt"]] == [120.0]
    assert [point["ttfb_ms"] for point in older["series"]["llm"]] == [2400.0]
    assert [point["ttfb_ms"] for point in older["series"]["tts"]] == [140.0]
    assert older["series"]["tts"][0]["processor"] == "AzureV2TTSService#0"
    assert older["series"]["tts"][0]["model"] == "en-US-Ava"
    assert older["sample_count"] == 3
    assert older["stats"]["stt"] == {"count": 1, "avg_ms": 120.0, "p95_ms": 120.0, "max_ms": 120.0}


def test_latency_report_handles_missing_file(tmp_path):
    report = build_latency_report(tmp_path / "missing.jsonl")
    assert report["exists"] is False
    assert report["sessions"] == []
    assert report["totals"] == {"sessions": 0, "samples": 0}


def test_latency_report_falls_back_to_processor_role_inference(tmp_path):
    log_path = tmp_path / "latency.jsonl"
    log_path.write_text(
        _line(event="ttfb", session="sess-a", processor="AzureV2TTSService#0", ttfb_ms=150.0, ts=1000.0),
        encoding="utf-8",
    )

    report = build_latency_report(log_path)
    assert [point["ttfb_ms"] for point in report["sessions"][0]["series"]["tts"]] == [150.0]
