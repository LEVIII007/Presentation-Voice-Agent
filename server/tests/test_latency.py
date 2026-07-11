"""Verifies LatencyObserver's timing math on synthetic frames.

The live audio loop can't run headlessly, so we drive the observer directly with
FramePushed events carrying controlled nanosecond timestamps and assert the
reported millisecond breakdown. Run: `python -m tests.test_latency` (or pytest).
"""

import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.observers.base_observer import FramePushed

from app.voice.latency import LatencyObserver

MS = 1_000_000  # ns per ms


class FakeLatencyLogger:
    """Duck-typed stand-in for LatencyLogger.event — records calls."""

    def __init__(self):
        self.events = []

    def event(self, name, *, session=None, human="", **fields):
        self.events.append({"name": name, "session": session, **fields})

    def turns(self):
        return [e for e in self.events if e["name"] == "turn"]

    def ttfbs(self):
        return [e for e in self.events if e["name"] == "ttfb"]


def _push(frame, ms):
    return FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=ms * MS)


async def _feed(obs, events):
    for frame, ms in events:
        await obs.on_push_frame(_push(frame, ms))


def test_full_turn_breakdown():
    log = FakeLatencyLogger()
    obs = LatencyObserver(log, "sess1")
    asyncio.run(_feed(obs, [
        (UserStoppedSpeakingFrame(), 1000),                        # t0
        (TranscriptionFrame(text="hi", user_id="u", timestamp=""), 1200),  # stt = 200ms
        (LLMFullResponseStartFrame(), 1250),
        (LLMTextFrame(text="Sure"), 1900),                         # llm_ttft = 1900-1200 = 700ms
        (TTSAudioRawFrame(audio=b"", sample_rate=16000, num_channels=1), 2000),  # tts_lead = 100ms
        (BotStartedSpeakingFrame(), 2050),                         # first_audio = 1050ms
        (BotStoppedSpeakingFrame(), 4000),                         # speak = 1950ms
    ]))
    turns = log.turns()
    assert len(turns) == 1, turns
    t = turns[0]
    assert t["reason"] == "complete"
    assert t["stt_ms"] == 200.0, t
    assert t["llm_ttft_ms"] == 700.0, t
    assert t["tts_lead_ms"] == 100.0, t
    assert t["first_audio_ms"] == 1050.0, t
    assert t["speak_ms"] == 1950.0, t
    print("ok: full turn breakdown", t)


def test_dedup_across_hops():
    """Same frame pushed over multiple edges must not restart the turn or
    double-count a milestone (first sighting wins)."""
    log = FakeLatencyLogger()
    obs = LatencyObserver(log, "s")
    u = UserStoppedSpeakingFrame()
    tr = TranscriptionFrame(text="x", user_id="u", timestamp="")
    asyncio.run(_feed(obs, [
        (u, 1000), (u, 1005), (u, 1010),        # 3 hops of the same UserStopped -> one turn @1000
        (tr, 1200), (tr, 1230),                  # 2 hops of transcript -> stt uses first (200ms)
        (BotStartedSpeakingFrame(), 1500),
        (BotStoppedSpeakingFrame(), 1600),
    ]))
    turns = log.turns()
    assert len(turns) == 1, turns
    assert turns[0]["stt_ms"] == 200.0, turns[0]
    assert turns[0]["first_audio_ms"] == 500.0, turns[0]
    print("ok: dedup across hops")


def test_tool_call_turn():
    log = FakeLatencyLogger()
    obs = LatencyObserver(log, "s")
    asyncio.run(_feed(obs, [
        (UserStoppedSpeakingFrame(), 0),
        (TranscriptionFrame(text="cost?", user_id="u", timestamp=""), 150),
        (FunctionCallInProgressFrame(function_name="go_to_slide", tool_call_id="1", arguments={}), 300),
        (FunctionCallResultFrame(function_name="go_to_slide", tool_call_id="1", arguments={}, result={}), 380),  # tool = 80ms
        (LLMTextFrame(text="Sure"), 500),
        (BotStartedSpeakingFrame(), 650),
        (BotStoppedSpeakingFrame(), 1200),
    ]))
    t = log.turns()[0]
    assert t["tool_ms"] == 80.0, t
    assert t["first_audio_ms"] == 650.0, t
    print("ok: tool-call turn", {"tool_ms": t["tool_ms"]})


def test_barge_in_emits_interrupted():
    """A new user turn arriving after the bot began speaking flushes the prior
    turn as 'interrupted' rather than silently dropping it."""
    log = FakeLatencyLogger()
    obs = LatencyObserver(log, "s")
    asyncio.run(_feed(obs, [
        (UserStoppedSpeakingFrame(), 0),
        (TranscriptionFrame(text="a", user_id="u", timestamp=""), 100),
        (BotStartedSpeakingFrame(), 400),
        (UserStoppedSpeakingFrame(), 900),   # barge-in: starts new turn, flushes old
    ]))
    reasons = [t["reason"] for t in log.turns()]
    assert "interrupted" in reasons, reasons
    print("ok: barge-in emits interrupted")


def test_metrics_ttfb():
    log = FakeLatencyLogger()
    obs = LatencyObserver(log, "s")
    mf = MetricsFrame(data=[TTFBMetricsData(processor="AzureV2TTSService", model="en-US-Ava", value=0.123)])
    asyncio.run(_feed(obs, [(mf, 5000)]))
    tt = log.ttfbs()
    assert len(tt) == 1 and tt[0]["ttfb_ms"] == 123.0, tt
    assert tt[0]["role"] == "tts", tt
    assert tt[0]["model"] == "en-US-Ava", tt
    print("ok: metrics ttfb", tt[0])


def test_greeting_without_user_turn_is_ignored():
    """The opening greeting has no preceding user turn; it must not crash or
    fabricate a turn."""
    log = FakeLatencyLogger()
    obs = LatencyObserver(log, "s")
    asyncio.run(_feed(obs, [
        (BotStartedSpeakingFrame(), 100),
        (BotStoppedSpeakingFrame(), 900),
    ]))
    assert log.turns() == [], log.turns()
    print("ok: greeting ignored")


if __name__ == "__main__":
    test_full_turn_breakdown()
    test_dedup_across_hops()
    test_tool_call_turn()
    test_barge_in_emits_interrupted()
    test_metrics_ttfb()
    test_greeting_without_user_turn_is_ignored()
    print("\nAll latency tests passed.")
