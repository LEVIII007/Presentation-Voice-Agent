"""Voice session runner: one Pipecat pipeline per WebSocket connection,
presenting a specific deck from the database.

Vendor-blind: STT/TTS/LLM arrive as injected factories (see
app/core/container.py — the only file that knows Deepgram/Cartesia/Azure
exist). The pipeline shape is the proven one from the original demo:
Silero VAD barge-in, greeting-only STT mute, RTVI slide-change pushes.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.aggregators.sentence import SentenceAggregator
from pipecat.processors.filters.stt_mute_filter import (
    STTMuteConfig,
    STTMuteFilter,
    STTMuteStrategy,
)
from pipecat.processors.frameworks.rtvi import (
    RTVIConfig,
    RTVIObserver,
    RTVIProcessor,
    RTVIServerMessageFrame,
)
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.utils.time import time_now_iso8601

from ..core.telemetry import LatencyLogger
from ..domain.models import DeckStatus
from ..domain.ports import DeckRepo, LLMFactory, SessionLog, STTFactory, TTSFactory
from ..services.prompts import build_greeting, build_system_prompt
from .latency import LatencyObserver
from .manager import ConnectionManager


def _slide_change_frame(slide_number: int) -> RTVIServerMessageFrame:
    """Build the RTVI message the browser listens for to switch slides."""
    return RTVIServerMessageFrame(
        data={"message_type": "go_to_slide", "slide": slide_number}
    )


def _is_slide_image_message(msg: Any) -> bool:
    """Identify the injected current-slide image message in the context."""
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return False
    content = msg.get("content")
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )


class VoiceSessionRunner:
    def __init__(
        self,
        *,
        stt: STTFactory,
        tts: TTSFactory,
        llm: LLMFactory,
        repo: DeckRepo,
        sessions: SessionLog,
        manager: ConnectionManager,
        latency: LatencyLogger,
    ):
        self._stt = stt
        self._tts = tts
        self._llm = llm
        self._repo = repo
        self._sessions = sessions
        self._manager = manager
        self._latency = latency

    async def run(self, websocket: Any, deck_id: str) -> None:
        if not self._manager.try_acquire():
            logger.warning(f"Rejecting session for {deck_id}: at capacity")
            await websocket.close(code=1013, reason="Presentation is at capacity")
            return
        try:
            await self._run_session(websocket, deck_id)
        finally:
            self._manager.release()

    async def _run_session(self, websocket: Any, deck_id: str) -> None:
        deck = await self._repo.get_deck(deck_id, with_slides=True)
        if deck is None or deck.status != DeckStatus.READY or not deck.slides:
            logger.warning(f"Rejecting session: deck {deck_id} not ready")
            await websocket.close(code=1008, reason="Deck not found or not ready")
            return
        slide_total = len(deck.slides)

        session_id = await self._sessions.start(deck_id)
        events: list[dict] = []
        turns: list[dict] = []  # {role, content, timestamp, slide} — the debug transcript
        slide_state = {"current": 1}  # mutable cell; closures below read/write it

        # --- Transport: audio in/out over the WebSocket, Silero VAD for barge-in ---
        vad = SileroVADAnalyzer(
            params=VADParams(confidence=0.7, start_secs=0.2, stop_secs=0.8, min_volume=0.6)
        )
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                vad_analyzer=vad,
                serializer=ProtobufFrameSerializer(),
            ),
        )

        stt = self._stt.create()
        tts = self._tts.create()
        llm = self._llm.create()

        # --- LLM context + the one tool the presenter can call ---
        go_to_slide_schema = FunctionSchema(
            name="go_to_slide",
            description=(
                "Change the slide currently shown to the audience. Call this when the "
                "user's question or request is best answered by a different slide, or "
                "when they ask to go to the next / previous slide."
            ),
            properties={
                "slide_number": {
                    "type": "integer",
                    "description": f"The slide to show, from 1 to {slide_total}.",
                }
            },
            required=["slide_number"],
        )
        context = LLMContext(
            messages=[{"role": "system", "content": build_system_prompt(deck)}],
            tools=ToolsSchema(standard_tools=[go_to_slide_schema]),
        )
        aggregator = LLMContextAggregatorPair(context)

        async def show_slide_image(n: int) -> None:
            """Keep exactly one slide image in the context: the slide on screen.

            Placed right after the system message so the prefix stays stable
            between slide changes (prompt-cache friendly). Replaced, never
            accumulated — old slides' pixels would bloat every request.
            """
            slide = deck.slides[n - 1]
            context.messages[:] = [
                m for m in context.messages if not _is_slide_image_message(m)
            ]
            if not slide.image_path or not Path(slide.image_path).exists():
                return
            png = await asyncio.to_thread(Path(slide.image_path).read_bytes)
            b64 = base64.b64encode(png).decode()
            context.messages.insert(
                1,
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"(System context, not spoken by the audience: this is the "
                                f"image of slide {n}, currently on screen. Use it to answer "
                                f"visual questions — diagrams, charts, layout, exact values "
                                f"— that the speaker notes may not cover. Never mention "
                                f"that you are shown images.)"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                },
            )

        rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

        # Mute STT only while the opening greeting plays, so the bot can't interrupt
        # itself before the user has said anything. After that, barge-in is fully live.
        stt_mute = STTMuteFilter(
            config=STTMuteConfig(strategies={STTMuteStrategy.MUTE_UNTIL_FIRST_BOT_COMPLETE})
        )

        # Records real spoken turns (not tool-call plumbing or image blobs), each
        # tagged with whichever slide was on screen when it happened — this is
        # what actually gets persisted for debugging, not raw LLM context.
        transcript = TranscriptProcessor()

        @transcript.event_handler("on_transcript_update")
        async def on_transcript_update(_processor, frame) -> None:
            for msg in frame.messages:
                turns.append(
                    {
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": msg.timestamp,
                        "slide": slide_state["current"],
                    }
                )

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                stt_mute,
                transcript.user(),
                aggregator.user(),
                rtvi,
                llm,
                SentenceAggregator(),  # smoother TTS: send whole sentences, not fragments
                tts,
                transport.output(),
                transcript.assistant(),
                aggregator.assistant(),
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,  # barge-in: user speech stops the bot immediately
                enable_metrics=True,  # feeds per-service TTFB to LatencyObserver
            ),
            observers=[RTVIObserver(rtvi), LatencyObserver(self._latency, session_id)],
        )

        # --- Tool handler: push the slide change to the browser, feed notes back ---
        async def handle_go_to_slide(params) -> None:
            requested = params.arguments.get("slide_number")
            try:
                n = int(requested)
            except (TypeError, ValueError):
                await params.result_callback({"error": f"Invalid slide number: {requested!r}"})
                return
            n = max(1, min(slide_total, n))  # clamp into range

            await task.queue_frame(_slide_change_frame(n))
            slide = deck.slides[n - 1]
            slide_state["current"] = n
            events.append(
                {
                    "type": "slide_change",
                    "slide": n,
                    "title": slide.title,
                    "timestamp": time_now_iso8601(),
                }
            )
            logger.info(f"go_to_slide -> {n} ({slide.title})")

            # Swap the in-context slide image before the follow-up completion,
            # so the answer to this very question is grounded in the new slide.
            await show_slide_image(n)

            await params.result_callback(
                {
                    "slide_number": n,
                    "title": slide.title,
                    "notes": slide.notes,
                    "instruction": "You are now on this slide. Answer using these notes, briefly.",
                }
            )

        llm.register_function("go_to_slide", handle_go_to_slide)

        # --- On connect: show slide 1 and greet ---
        @transport.event_handler("on_client_connected")
        async def on_client_connected(_transport, _client):
            await asyncio.sleep(1.5)  # let the pipeline's StartFrame settle before pushing
            await task.queue_frame(_slide_change_frame(1))
            await show_slide_image(1)
            greeting = build_greeting(deck)
            await task.queue_frame(LLMFullResponseStartFrame())
            await task.queue_frame(TextFrame(greeting))
            await task.queue_frame(LLMFullResponseEndFrame())
            context.messages.append({"role": "assistant", "content": greeting})

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport, _client):
            logger.info("Client disconnected; cancelling task")
            await task.cancel()

        runner = PipelineRunner(handle_sigint=False)
        try:
            await runner.run(task)
        finally:
            try:
                await self._sessions.finish(session_id, turns, events)
            except Exception:
                logger.exception("Failed to persist session transcript")
