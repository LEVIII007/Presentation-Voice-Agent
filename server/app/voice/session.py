"""Voice session runner: one Pipecat pipeline per WebSocket connection,
presenting a specific deck from the database.

Vendor-blind: STT/TTS/LLM arrive as injected factories (see
app/core/container.py — the only file that knows Deepgram/Cartesia/Azure
exist). The pipeline shape is the proven one from the original demo:
Silero VAD barge-in, greeting-only STT mute, RTVI slide-change pushes.

The presentation runs on AUTOPILOT: an activity watcher tracks whether the
bot is speaking, an LLM completion is in flight, or the user is talking;
whenever everything goes quiet with autopilot engaged, a watchdog injects a
stage-direction cue ("present slide N now") and triggers the next completion.
Barge-in interrupts it; a pause tool stops it; the flow resumes on its own.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    TTSSpeakFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
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
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
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
from pipecat.utils.string import match_endofsentence
from pipecat.utils.time import time_now_iso8601

from ..core.telemetry import LatencyLogger
from ..domain.models import DeckStatus
from ..domain.ports import DeckRepo, LLMFactory, SessionLog, STTFactory, TTSFactory
from ..services.prompts import (
    build_advance_cue,
    build_closing_cue,
    build_kickoff_cue,
    build_system_prompt,
)
from .latency import LatencyObserver
from .manager import ConnectionManager
from .slide_images import (
    SlideImageContextState,
    clear_slide_image,
    clear_temporary_slide_image,
    set_slide_image,
)

# Seconds of full quiet before the autopilot advances to the next slide; the
# longer variant applies right after user speech, leaving room for follow-ups
# (and for STT to finish transcribing).
_ADVANCE_QUIET_SECS = 1.6
_ADVANCE_QUIET_AFTER_USER_SECS = 4.0
_USER_RECENT_SECS = 6.0
_EXPECT_RUN_TIMEOUT_SECS = 10.0  # self-heal if an expected completion never starts


def _slide_change_frame(slide_number: int) -> RTVIServerMessageFrame:
    """Build the RTVI message the browser listens for to switch slides."""
    return RTVIServerMessageFrame(
        data={"message_type": "go_to_slide", "slide": slide_number}
    )


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using the same detector the TTS path uses, so a
    'sentence' here is exactly what the presenter spoke as one unit."""
    sentences: list[str] = []
    rest = text.strip()
    while rest:
        idx = match_endofsentence(rest)
        if idx <= 0:  # no complete sentence left; keep the remainder as one
            sentences.append(rest)
            break
        sentences.append(rest[:idx].strip())
        rest = rest[idx:].strip()
    return [s for s in sentences if s]


def _completed_sentence_count(text: str) -> int:
    """How many *finished* sentences the text contains — a trailing fragment with
    no sentence-ending punctuation does not count. This is the index of the
    interrupted sentence within the full narration when `text` is what was spoken."""
    count = 0
    rest = text.strip()
    while rest:
        idx = match_endofsentence(rest)
        if idx <= 0:  # trailing fragment, still mid-sentence
            break
        count += 1
        rest = rest[idx:].strip()
    return count


def _last_assistant_text(messages: list) -> str:
    """The most recent assistant turn's spoken text, used to replay on resume."""
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if content.strip():
                return content
        elif isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            joined = " ".join(t for t in parts if t).strip()
            if joined:
                return joined
    return ""


class _PresenterState:
    """Live activity flags the autopilot watchdog reads to decide 'is it quiet'."""

    def __init__(self) -> None:
        self.bot_speaking = False
        self.llm_in_flight = False
        self.fn_pending = 0
        self.user_speaking = False
        self.last_user_ts = 0.0
        # Text actually spoken (TTS, playback-aligned) in the current turn, reset
        # at each new LLM response. A hard pause reads this to know which sentence
        # was cut off, so Resume can replay from its start.
        self.spoken_text = ""
        # True while a completion is known to be coming (kickoff queued, cue
        # queued, or a tool result about to trigger the follow-up run).
        self.expecting_run = False
        self.expect_ts = 0.0

    def expect_run(self) -> None:
        self.expecting_run = True
        self.expect_ts = asyncio.get_running_loop().time()

    def busy(self) -> bool:
        now = asyncio.get_running_loop().time()
        if self.expecting_run and now - self.expect_ts > _EXPECT_RUN_TIMEOUT_SECS:
            self.expecting_run = False
        return (
            self.bot_speaking
            or self.llm_in_flight
            or self.fn_pending > 0
            or self.user_speaking
            or self.expecting_run
        )


class _ActivityWatcher(FrameProcessor):
    """Passive tap at the end of the pipeline that keeps _PresenterState current."""

    def __init__(
        self,
        state: _PresenterState,
        *,
        on_llm_response_start: Callable[[], Awaitable[None]] | None = None,
    ):
        super().__init__()
        self._state = state
        self._on_llm_response_start = on_llm_response_start

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        st = self._state
        if isinstance(frame, BotStartedSpeakingFrame):
            st.bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            st.bot_speaking = False
        elif isinstance(frame, LLMFullResponseStartFrame):
            st.llm_in_flight = True
            st.expecting_run = False
            st.spoken_text = ""  # fresh turn; forget the previous narration
            if self._on_llm_response_start is not None:
                await self._on_llm_response_start()
        elif isinstance(frame, LLMFullResponseEndFrame):
            st.llm_in_flight = False
        elif isinstance(frame, FunctionCallInProgressFrame):
            st.fn_pending += 1
        elif isinstance(frame, FunctionCallResultFrame):
            st.fn_pending = max(0, st.fn_pending - 1)
            st.expect_run()  # pipecat auto-runs the LLM on the result
        elif isinstance(frame, UserStartedSpeakingFrame):
            st.user_speaking = True
            st.last_user_ts = asyncio.get_running_loop().time()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            st.user_speaking = False
            st.last_user_ts = asyncio.get_running_loop().time()
        elif isinstance(frame, TTSTextFrame):
            # This tap sits after transport.output(), so TTS text arrives in sync
            # with playback — it reflects what the audience has actually heard.
            st.spoken_text = f"{st.spoken_text} {frame.text}".strip() if st.spoken_text else frame.text
        await self.push_frame(frame, direction)


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
        always_show_slide_image: bool = False,
    ):
        self._stt = stt
        self._tts = tts
        self._llm = llm
        self._repo = repo
        self._sessions = sessions
        self._manager = manager
        self._latency = latency
        self._always_show_slide_image = always_show_slide_image

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
        # Slides substantively presented or discussed so far — includes both the
        # linear walk and any Q&A jumps. When the linear flow later reaches one of
        # these, the presenter acknowledges it instead of repeating it verbatim.
        covered: set[int] = set()

        # Autopilot: engaged on connect, disengaged by the pause tool or after
        # the closing. progress["next"] is the next slide in the LINEAR flow —
        # Q&A jumps move slide_state but not progress, so after a digression
        # the talk resumes where it left off.
        state = _PresenterState()
        autopilot = {"on": False}
        progress = {"next": 2, "outro_done": False}  # kickoff presents slide 1

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
        slide_image_state = SlideImageContextState()

        # --- LLM context + the tools the presenter can call ---
        go_to_slide_schema = FunctionSchema(
            name="go_to_slide",
            description=(
                "Switch the slide shown to the audience. Call this PROACTIVELY, on your "
                "own, without being asked: whenever an audience question is best answered "
                "by a slide other than the one on screen, call this FIRST (before you "
                "answer) to bring that slide up. Also call it when they ask to go to the "
                "next / previous slide or to jump to a specific topic."
            ),
            properties={
                "slide_number": {
                    "type": "integer",
                    "description": f"The slide to show, from 1 to {slide_total}.",
                }
            },
            required=["slide_number"],
        )
        set_flow_schema = FunctionSchema(
            name="set_presentation_flow",
            description=(
                "Pause or resume the automatic flow of the presentation. Call with "
                "'pause' when the audience asks to pause, stop, or hold on; call with "
                "'resume' when they ask to continue the presentation."
            ),
            properties={
                "action": {
                    "type": "string",
                    "enum": ["pause", "resume"],
                    "description": "Whether to pause or resume the presentation flow.",
                }
            },
            required=["action"],
        )
        tool_schemas = [go_to_slide_schema, set_flow_schema]
        if not self._always_show_slide_image:
            tool_schemas.append(
                FunctionSchema(
                    name="look_at_slide",
                    description=(
                        "Inspect a slide image for a visual detail the notes do not fully "
                        "cover, such as a chart shape, diagram path, layout, or exact "
                        "on-screen value. Use this only when you truly need pixels. Pass "
                        "slide_number only when you need a specific slide's image."
                    ),
                    properties={
                        "slide_number": {
                            "type": "integer",
                            "description": (
                                f"Optional. The slide image to inspect, from 1 to {slide_total}. "
                                "If omitted, inspect the slide currently on screen."
                            ),
                        }
                    },
                    required=[],
                )
            )
        context = LLMContext(
            messages=[
                {
                    "role": "system",
                    "content": build_system_prompt(
                        deck, always_show_slide_image=self._always_show_slide_image
                    ),
                }
            ],
            tools=ToolsSchema(standard_tools=tool_schemas),
        )
        aggregator = LLMContextAggregatorPair(context)

        def resolve_slide_number(requested: Any, *, default: int | None = None) -> int:
            if requested in (None, ""):
                if default is None:
                    raise ValueError("missing slide number")
                return default
            try:
                n = int(requested)
            except (TypeError, ValueError) as e:
                raise ValueError(f"invalid slide number: {requested!r}") from e
            return max(1, min(slide_total, n))

        async def set_slide_image_context(n: int, *, temporary: bool) -> bool:
            """Keep exactly one slide image in the context, after the system prompt."""

            slide = deck.slides[n - 1]
            clear_slide_image(context.messages, slide_image_state)
            if not slide.image_path or not Path(slide.image_path).exists():
                return False
            png = await asyncio.to_thread(Path(slide.image_path).read_bytes)
            b64 = base64.b64encode(png).decode()
            set_slide_image(
                context.messages,
                slide_image_state,
                slide_number=n,
                image_b64=b64,
                temporary=temporary,
            )
            return True

        async def expire_temporary_slide_image() -> None:
            clear_temporary_slide_image(context.messages, slide_image_state)

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
                _ActivityWatcher(
                    state,
                    on_llm_response_start=expire_temporary_slide_image,
                ),  # feeds the autopilot watchdog
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
                n = resolve_slide_number(requested)
            except ValueError:
                await params.result_callback({"error": f"Invalid slide number: {requested!r}"})
                return

            await task.queue_frame(_slide_change_frame(n))
            slide = deck.slides[n - 1]
            slide_state["current"] = n
            covered.add(n)  # a Q&A jump counts as having discussed this slide
            events.append(
                {
                    "type": "slide_change",
                    "slide": n,
                    "title": slide.title,
                    "timestamp": time_now_iso8601(),
                }
            )
            logger.info(f"go_to_slide -> {n} ({slide.title})")

            if self._always_show_slide_image:
                await set_slide_image_context(n, temporary=False)

            await params.result_callback(
                {
                    "slide_number": n,
                    "title": slide.title,
                    "transition": slide.transition,
                    "notes": slide.notes,
                    "instruction": "You are now on this slide.",
                }
            )

        async def handle_look_at_slide(params) -> None:
            requested = params.arguments.get("slide_number")
            try:
                n = resolve_slide_number(requested, default=slide_state["current"])
            except ValueError:
                await params.result_callback({"error": f"Invalid slide number: {requested!r}"})
                return

            slide = deck.slides[n - 1]
            image_available = await set_slide_image_context(n, temporary=True)
            events.append(
                {
                    "type": "slide_image_lookup",
                    "slide": n,
                    "title": slide.title,
                    "image_available": image_available,
                    "timestamp": time_now_iso8601(),
                }
            )
            logger.info(f"look_at_slide -> {n} ({slide.title}) image={image_available}")
            await params.result_callback(
                {
                    "slide_number": n,
                    "title": slide.title,
                    "notes": slide.notes,
                    "image_available": image_available,
                    "instruction": (
                        "Use this image for the next reply only, only for the visual detail "
                        "you needed, and never mention images or tools."
                        if image_available
                        else (
                            "No image is available for this slide. Answer from the notes "
                            "alone, and say the deck does not show the visual detail if "
                            "needed."
                        )
                    ),
                }
            )

        async def handle_set_presentation_flow(params) -> None:
            action = str(params.arguments.get("action", "")).lower()
            if action not in ("pause", "resume"):
                await params.result_callback({"error": f"Unknown action: {action!r}"})
                return
            autopilot["on"] = action == "resume"
            events.append({"type": f"presentation_{action}", "timestamp": time_now_iso8601()})
            logger.info(f"Presentation flow: {action}")
            await params.result_callback({"ok": True, "presentation": action})

        llm.register_function("go_to_slide", handle_go_to_slide)
        llm.register_function("set_presentation_flow", handle_set_presentation_flow)
        if not self._always_show_slide_image:
            llm.register_function("look_at_slide", handle_look_at_slide)

        # --- Stage directions: append a cue to the context and trigger a run ---
        async def send_cue(text: str) -> None:
            clear_temporary_slide_image(context.messages, slide_image_state)
            context.messages.append({"role": "user", "content": text})
            state.expect_run()
            await task.queue_frame(LLMRunFrame())

        # --- Hard pause/resume from the browser buttons (bypasses the LLM) ---
        # A spoken "pause" already gets an instant cut for free: the user's voice
        # trips VAD, which barges in and stops the bot. The on-screen buttons have
        # no speech to trip VAD, so they drive the pipeline directly here: pause
        # interrupts mid-sentence and remembers where narration was cut; resume
        # re-speaks from the start of that sentence.
        resume_buffer = {"text": ""}

        async def do_pause() -> None:
            resume_text = ""
            if state.bot_speaking:
                spoken = state.spoken_text.strip()
                full = _last_assistant_text(context.messages)
                if full:
                    sentences = _split_sentences(full)
                    # Count only sentences finished before the cut; the next one is
                    # the sentence that got interrupted, so replay starts there.
                    done = _completed_sentence_count(spoken) if spoken else 0
                    done = max(0, min(done, len(sentences)))
                    resume_text = " ".join(sentences[done:]).strip()
            resume_buffer["text"] = resume_text
            autopilot["on"] = False
            await rtvi.interrupt_bot()  # cut the audio mid-sentence, right now
            events.append({"type": "presentation_pause", "timestamp": time_now_iso8601()})
            logger.info(f"Presentation paused (replay buffer: {len(resume_text)} chars)")

        async def do_resume() -> None:
            resume_text = resume_buffer["text"]
            resume_buffer["text"] = ""
            events.append({"type": "presentation_resume", "timestamp": time_now_iso8601()})
            if resume_text:
                # Re-speak the interrupted sentence (and the rest of that turn)
                # verbatim via TTS — no LLM round-trip, so the words match exactly
                # what the audience was hearing. queue the speech first, then
                # re-arm autopilot: TTS starts well within the watchdog's quiet
                # window, so it won't skip ahead before the replay begins.
                await task.queue_frame(TTSSpeakFrame(resume_text))
                logger.info(f"Presentation resumed; replaying {len(resume_text)} chars")
            else:
                logger.info("Presentation resumed; nothing buffered to replay")
            autopilot["on"] = True

        @rtvi.event_handler("on_client_message")
        async def on_client_message(rtvi_proc, message) -> None:
            if message.type != "presentation-flow":
                return
            data = message.data or {}
            action = str(data.get("action", "")).lower()
            if action == "pause":
                await do_pause()
            elif action == "resume":
                await do_resume()
            else:
                await rtvi_proc.send_error_response(message, f"Unknown action: {action!r}")
                return
            await rtvi_proc.send_server_response(message, {"ok": True, "presentation": action})

        # --- On connect: show slide 1, then let the model open the talk ---
        @transport.event_handler("on_client_connected")
        async def on_client_connected(_transport, _client):
            await asyncio.sleep(1.5)  # let the pipeline's StartFrame settle before pushing
            await task.queue_frame(_slide_change_frame(1))
            if self._always_show_slide_image:
                await set_slide_image_context(1, temporary=False)
            covered.add(1)  # the kickoff presents slide 1
            await send_cue(build_kickoff_cue())
            autopilot["on"] = True

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport, _client):
            logger.info("Client disconnected; cancelling task")
            await task.cancel()

        # --- Autopilot watchdog: when everything goes quiet, cue the next slide.
        # Poll-based rather than event-based so a missed frame can never stall
        # the talk; user speech and in-flight completions always take priority.
        async def autopilot_loop() -> None:
            loop = asyncio.get_running_loop()
            idle_since: float | None = None
            while True:
                await asyncio.sleep(0.5)
                if not autopilot["on"] or state.busy():
                    idle_since = None
                    continue
                now = loop.time()
                if idle_since is None:
                    idle_since = now
                    continue
                quiet = (
                    _ADVANCE_QUIET_AFTER_USER_SECS
                    if now - state.last_user_ts < _USER_RECENT_SECS
                    else _ADVANCE_QUIET_SECS
                )
                if now - idle_since < quiet:
                    continue
                idle_since = None
                n = progress["next"]
                if n <= slide_total:
                    progress["next"] = n + 1
                    revisit = n in covered  # a Q&A jump already showed this slide
                    covered.add(n)
                    events.append(
                        {
                            "type": "auto_advance",
                            "slide": n,
                            "revisit": revisit,
                            "timestamp": time_now_iso8601(),
                        }
                    )
                    await send_cue(build_advance_cue(n, already_covered=revisit))
                elif not progress["outro_done"]:
                    progress["outro_done"] = True
                    autopilot["on"] = False  # Q&A from here; the user drives
                    events.append({"type": "closing", "timestamp": time_now_iso8601()})
                    await send_cue(build_closing_cue())

        watchdog = asyncio.create_task(autopilot_loop(), name="autopilot-watchdog")

        runner = PipelineRunner(handle_sigint=False)
        try:
            await runner.run(task)
        finally:
            watchdog.cancel()
            try:
                await self._sessions.finish(session_id, turns, events)
            except Exception:
                logger.exception("Failed to persist session transcript")
