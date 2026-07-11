"""Azure Speech TTS adapter — WebSocket V2 text-stream API, with word-level
caption timestamps.

Kept in its own module so importing the heavy `azure-cognitiveservices-speech`
SDK only happens when TTS_PROVIDER=azure. The container imports this lazily;
the Cartesia path never loads the SDK.

Two things this service gets right that pipecat's stock AzureTTSService does not:

1. Crash: pipecat's classic service calls `speak_ssml_async` and *discards* the
   future, so on Python 3.13 the GC frees it mid-synthesis → segfault. We use
   the low-latency V2 text-stream API and hold the task for the whole utterance.

2. Captions: this extends `WordTTSService` (the same family Cartesia and
   ElevenLabs use), feeding Azure's word-boundary events via
   `add_word_timestamps`. pipecat then paces caption `TTSTextFrame`s to the
   audio clock. A plain `TTSService` (our first cut) emits the whole sentence's
   caption only *after* its audio, so captions trail ~one sentence behind.

The V2 endpoint is region-locked: wss://{region}.tts.speech.microsoft.com/
cognitiveservices/websocket/v2 — hence a region, not a custom endpoint.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

from azure.cognitiveservices.speech import (
    PropertyId,
    SpeechConfig,
    SpeechSynthesisBoundaryType,
    SpeechSynthesisOutputFormat,
    SpeechSynthesisRequest,
    SpeechSynthesisRequestInputType,
    SpeechSynthesizer,
)
from loguru import logger
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
)
from pipecat.services.tts_service import WordTTSService

# 100-nanosecond ticks per second — Azure word-boundary offsets are in ticks.
_TICKS_PER_SECOND = 10_000_000


@dataclass
class _Err:
    details: str


def _output_format(sample_rate: int) -> SpeechSynthesisOutputFormat:
    return {
        16000: SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm,
        22050: SpeechSynthesisOutputFormat.Raw22050Hz16BitMonoPcm,
        24000: SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm,
        44100: SpeechSynthesisOutputFormat.Raw44100Hz16BitMonoPcm,
        48000: SpeechSynthesisOutputFormat.Raw48Khz16BitMonoPcm,
    }.get(sample_rate, SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm)


class AzureWordTTSService(WordTTSService):
    """Azure TTS over the V2 text-stream API with word-timestamped captions."""

    def __init__(
        self,
        *,
        api_key: str,
        region: str,
        voice: str,
        sample_rate: int = 24000,
        **kwargs,
    ):
        # push_text_frames=False: don't let the base emit the whole-sentence
        # caption; the words task re-emits per-word frames (and the end frames)
        # with audio-aligned pts instead.
        super().__init__(
            sample_rate=sample_rate, push_text_frames=False, **kwargs
        )
        self._api_key = api_key
        self._region = region
        self._voice = voice
        self._synth: Optional[SpeechSynthesizer] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None  # current utterance's sink

    def can_generate_metrics(self) -> bool:
        return True

    async def start(self, frame: StartFrame):
        await super().start(frame)  # creates the WordTTSService words task
        if self._synth:
            return

        self._loop = asyncio.get_running_loop()
        endpoint = (
            f"wss://{self._region}.tts.speech.microsoft.com"
            "/cognitiveservices/websocket/v2"
        )
        cfg = SpeechConfig(endpoint=endpoint, subscription=self._api_key)
        # V2 text-stream doesn't support SSML: voice + format are set here.
        cfg.speech_synthesis_voice_name = self._voice
        cfg.set_speech_synthesis_output_format(_output_format(self.sample_rate))
        # Guard against the SDK cancelling while text streams in with gaps.
        # These PropertyIds only exist in speech SDK >=1.50; pipecat pins 1.42,
        # where they're absent — and our write-then-close pattern has no gaps,
        # so skipping them is harmless. Set them only when available.
        for prop_name, value in (
            ("SpeechSynthesis_FrameTimeoutInterval", "100000000"),
            ("SpeechSynthesis_RtfTimeoutThreshold", "10"),
        ):
            prop = getattr(PropertyId, prop_name, None)
            if prop is not None:
                cfg.set_property(prop, value)

        self._synth = SpeechSynthesizer(speech_config=cfg, audio_config=None)
        # Handlers fire on native SDK threads → hop back to the event loop.
        self._synth.synthesizing.connect(self._on_synthesizing)
        self._synth.synthesis_word_boundary.connect(self._on_word_boundary)
        self._synth.synthesis_completed.connect(self._on_completed)
        self._synth.synthesis_canceled.connect(self._on_canceled)

    def _put(self, item) -> None:
        q, loop = self._queue, self._loop
        if q is not None and loop is not None:
            loop.call_soon_threadsafe(q.put_nowait, item)

    def _on_synthesizing(self, evt) -> None:
        if evt.result and evt.result.audio_data:
            self._put(("audio", evt.result.audio_data))

    def _on_word_boundary(self, evt) -> None:
        # Sentence boundaries would duplicate the words; keep words + punctuation.
        if evt.boundary_type == SpeechSynthesisBoundaryType.Sentence:
            return
        if evt.text:
            self._put(("word", evt.text, evt.audio_offset / _TICKS_PER_SECOND))

    def _on_completed(self, evt) -> None:
        self._put(("done",))

    def _on_canceled(self, evt) -> None:
        cd = evt.result.cancellation_details
        logger.error(f"{self}: synthesis canceled: {cd.reason} — {cd.error_details}")
        self._put(("error", _Err(cd.error_details)))

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        logger.debug(f"{self}: Generating TTS [{text}]")
        self._queue = asyncio.Queue()

        await self.start_ttfb_metrics()
        yield TTSStartedFrame()

        req = SpeechSynthesisRequest(
            input_type=SpeechSynthesisRequestInputType.TextStream
        )
        # Hold `task` for the whole generator so the native op is never GC'd
        # mid-flight — the exact failure pipecat's own service hits.
        task = self._synth.speak_async(req)
        req.input_stream.write(text)
        req.input_stream.close()
        await self.start_tts_usage_metrics(text)

        started_words = False
        completed = False
        try:
            while True:
                item = await self._queue.get()
                tag = item[0]
                if tag == "audio":
                    if not started_words:
                        # Anchor caption timing to when audio playback begins.
                        await self.start_word_timestamps()
                        started_words = True
                    await self.stop_ttfb_metrics()
                    yield TTSAudioRawFrame(
                        audio=item[1], sample_rate=self.sample_rate, num_channels=1
                    )
                elif tag == "word":
                    await self.add_word_timestamps([(item[1], item[2])])
                elif tag == "done":
                    completed = True
                    break
                elif tag == "error":
                    yield ErrorFrame(error=f"Azure TTS canceled: {item[1].details}")
                    break
        finally:
            self._queue = None
            # Block on the native task off-thread before dropping our reference.
            await asyncio.to_thread(task.get)

        if completed:
            # Re-emit TTSStopped + LLMFullResponseEnd with pts *after* the last
            # word, so the caption isn't cut short. (Same sentinels Cartesia uses.)
            await self.add_word_timestamps([("TTSStoppedFrame", 0), ("Reset", 0)])


class AzureTTSFactory:
    def __init__(self, *, api_key: str, region: str, voice: str):
        self._kwargs = dict(api_key=api_key, region=region, voice=voice)

    def create(self) -> AzureWordTTSService:
        return AzureWordTTSService(**self._kwargs)
