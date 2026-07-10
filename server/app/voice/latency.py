"""LatencyObserver — measures where a conversational turn actually spends time.

Watches frames flowing through the Pipecat pipeline and, for each turn (from the
moment the user stops speaking to the moment the bot finishes replying), reports
the breakdown:

    user_stopped ──stt──▶ transcript ──llm(+reasoning)──▶ first token
                 ──tts──▶ first audio out (BotStartedSpeaking)   ← headline number

Plus per-service TTFB straight from Pipecat's own metrics (enable_metrics=True).

All timings come from FramePushed.timestamp, the pipeline's monotonic clock, in
nanoseconds; we report milliseconds. on_push_frame runs on the hot audio path,
so it must be cheap and must never raise.
"""

from __future__ import annotations

from typing import Optional

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
from pipecat.observers.base_observer import BaseObserver, FramePushed

_NS_PER_MS = 1_000_000


def _ms(ns: int) -> float:
    return round(ns / _NS_PER_MS, 1)


class LatencyObserver(BaseObserver):
    def __init__(self, latency_logger, session_id: str):
        super().__init__()
        self._log = latency_logger
        self._sid = session_id
        self._turn: Optional[dict] = None
        self._turn_frame_id = None

    def _start_turn(self, t0_ns: int, frame_id) -> None:
        # A barge-in can start a new turn before the last one finished replying.
        if self._turn is not None and "first_audio_ns" in self._turn:
            self._emit("interrupted")
        self._turn = {"t0_ns": t0_ns}
        self._turn_frame_id = frame_id

    def _mark(self, key: str, ts_ns: int) -> None:
        """Record the first occurrence of a milestone in the current turn."""
        if self._turn is not None and key not in self._turn:
            self._turn[key] = ts_ns

    def _emit(self, reason: str) -> None:
        t = self._turn
        self._turn = None
        self._turn_frame_id = None
        if not t or "t0_ns" not in t:
            return
        t0 = t["t0_ns"]
        rel = lambda k: _ms(t[k] - t0) if k in t else None  # noqa: E731

        stt = rel("transcript_ns")
        first_token = rel("llm_first_token_ns")
        first_audio = rel("first_audio_ns")
        # LLM latency = transcript -> first token (includes reasoning + network)
        llm_ttft = (
            _ms(t["llm_first_token_ns"] - t["transcript_ns"])
            if "llm_first_token_ns" in t and "transcript_ns" in t
            else None
        )
        tts_lead = (
            _ms(t["tts_first_audio_ns"] - t["llm_first_token_ns"])
            if "tts_first_audio_ns" in t and "llm_first_token_ns" in t
            else None
        )
        tool_ms = (
            _ms(t["tool_end_ns"] - t["tool_start_ns"])
            if "tool_end_ns" in t and "tool_start_ns" in t
            else None
        )
        speak_ms = (
            _ms(t["bot_stopped_ns"] - t["first_audio_ns"])
            if "bot_stopped_ns" in t and "first_audio_ns" in t
            else None
        )

        human = (
            f"[latency] turn ({reason}): first_audio={first_audio}ms "
            f"| stt={stt} llm_ttft={llm_ttft} tts_lead={tts_lead}"
            f"{f' tool={tool_ms}' if tool_ms is not None else ''} speak={speak_ms}"
        )
        self._log.event(
            "turn",
            session=self._sid,
            human=human,
            reason=reason,
            first_audio_ms=first_audio,
            stt_ms=stt,
            llm_ttft_ms=llm_ttft,
            tts_lead_ms=tts_lead,
            tool_ms=tool_ms,
            speak_ms=speak_ms,
        )

    async def on_push_frame(self, data: FramePushed) -> None:
        try:
            frame = data.frame
            ts = data.timestamp

            if isinstance(frame, UserStoppedSpeakingFrame):
                if frame.id != self._turn_frame_id:  # dedup across pipeline hops
                    self._start_turn(ts, frame.id)
                return

            if isinstance(frame, MetricsFrame):
                for m in frame.data or []:
                    if isinstance(m, TTFBMetricsData) and m.value:
                        self._log.event(
                            "ttfb",
                            session=self._sid,
                            human=f"[latency] ttfb {m.processor}={_ms(int(m.value * 1e9))}ms",
                            processor=m.processor,
                            ttfb_ms=round(m.value * 1000, 1),
                        )
                return

            if self._turn is None:
                return  # e.g. the opening greeting — no user turn to attribute it to

            if isinstance(frame, TranscriptionFrame):
                self._mark("transcript_ns", ts)
            elif isinstance(frame, LLMFullResponseStartFrame):
                self._mark("llm_start_ns", ts)
            elif isinstance(frame, LLMTextFrame):
                self._mark("llm_first_token_ns", ts)
            elif isinstance(frame, FunctionCallInProgressFrame):
                self._mark("tool_start_ns", ts)
            elif isinstance(frame, FunctionCallResultFrame):
                self._mark("tool_end_ns", ts)
            elif isinstance(frame, TTSAudioRawFrame):
                self._mark("tts_first_audio_ns", ts)
            elif isinstance(frame, BotStartedSpeakingFrame):
                self._mark("first_audio_ns", ts)
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._mark("bot_stopped_ns", ts)
                self._emit("complete")
        except Exception:
            # Never let measurement disrupt the audio path.
            pass
