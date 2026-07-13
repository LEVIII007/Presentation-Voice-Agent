"""Voice session runner: one Pipecat pipeline per WebSocket connection,
presenting a specific deck from the database.

Vendor-blind: STT/TTS/LLM arrive as injected factories (see
app/core/container.py — the only file that knows Deepgram/Cartesia/Azure
exist). The pipeline shape is the proven one from the original demo:
Silero VAD barge-in, RTVI slide-change pushes.

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
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
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
from pipecat.utils.time import time_now_iso8601

from ..core.telemetry import LatencyLogger
from ..domain.models import DeckStatus
from ..domain.ports import (
    DeckRepo,
    LLMFactory,
    QAExtractor,
    SessionLog,
    STTFactory,
    TTSFactory,
)
from ..services.prompts import (
    build_advance_cue,
    build_closing_cue,
    build_finish_cue,
    build_kickoff_cue,
    build_system_prompt,
)
from .history import recent_turns as _recent_turns
from .latency import LatencyObserver
from .manager import ConnectionManager
from .slide_images import (
    SlideImageContextState,
    clear_slide_image,
    clear_temporary_slide_image,
    set_slide_image,
)
from .state import PresentationState

# Seconds of full quiet before the autopilot advances to the next slide; the
# longer variant applies right after user speech, leaving room for follow-ups
# (and for STT to finish transcribing).
_ADVANCE_QUIET_SECS = 1.6
_ADVANCE_QUIET_AFTER_USER_SECS = 4.0
_USER_RECENT_SECS = 6.0
_EXPECT_RUN_TIMEOUT_SECS = 10.0  # self-heal if an expected completion never starts
_QA_SETTLE_SECS = 2.0  # let short answer follow-through land before logging
# Beat pacing: the MODEL decides where a pause belongs and how long it is — by
# writing the TTS backend's own pause markup directly into its narration (see
# the presentation-flow prompt), wherever a brief pause would feel natural.
# That markup is just text: it rides straight through this pipeline like any
# other narration and the backend's TTS engine renders the pause itself, so no
# custom frame handling is needed here at all. build_system_prompt is only told
# the exact markup to use (via TTSFactory.pause_tag — the one place this
# session asks the injected TTS factory for that capability, never by vendor
# name) — on a backend with no such markup (e.g. Azure's V2 text-stream API,
# which has no SSML support), that instruction is simply omitted from the
# prompt, so the model never emits markup that backend would misread as words.
_BEAT_PAUSE_EXAMPLE_SECS = 0.6  # duration used only to show the model the exact syntax


def _slide_change_frame(slide_number: int) -> RTVIServerMessageFrame:
    """Build the RTVI message the browser listens for to switch slides."""
    return RTVIServerMessageFrame(
        data={"message_type": "go_to_slide", "slide": slide_number}
    )

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


def _message_field(message: Any, key: str, default: Any = None) -> Any:
    """Read a field from either an object-like RTVI message or a plain dict."""
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _parse_client_message(message: Any, expected: str) -> tuple[str | None, dict[str, Any]]:
    """Normalize a browser client message across transport shapes, matching a
    specific inner type (e.g. 'presentation-flow', 'manual-slide'). Returns the
    matched type and its data payload, or (None, {}) if it doesn't match."""
    message_type = _message_field(message, "type")
    data = _message_field(message, "data") or {}
    if not isinstance(data, dict):
        data = {}

    if message_type == expected:
        return message_type, data

    if message_type != "client-message":
        return None, {}

    inner_type = data.get("t") or data.get("type") or data.get("message_type")
    inner_data = data.get("d", data.get("data"))
    if inner_type != expected:
        return None, {}
    return str(inner_type), inner_data if isinstance(inner_data, dict) else {}


class _PresenterState:
    """Live activity flags the autopilot watchdog reads to decide 'is it quiet'.
    Pipeline liveness only — presentation position lives in PresentationState."""

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
        # Full text the LLM generated this turn (streaming, ahead of playback),
        # kept by _GenTextTap. Diffed against spoken_text on a barge-in to know
        # which sentences the audience never heard.
        self.gen_text = ""
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
        on_bot_started_speaking: Callable[[], None] | None = None,
        on_user_started_speaking: Callable[[], None] | None = None,
    ):
        super().__init__()
        self._state = state
        self._on_llm_response_start = on_llm_response_start
        self._on_bot_started_speaking = on_bot_started_speaking
        self._on_user_started_speaking = on_user_started_speaking

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        st = self._state
        if isinstance(frame, InterruptionFrame):
            # A barge-in (or a hard pause via rtvi.interrupt_bot) cancels the
            # current turn and drains queued frames, so the LLMFullResponseEnd /
            # BotStoppedSpeaking / FunctionCallResult frames that would normally
            # clear these liveness flags can be dropped before they reach this
            # tap. Reset them off the interruption itself — the one signal the
            # interruption path guarantees reaches us — or a flag stays stuck
            # True and busy() wedges the autopilot forever (the noise-barge-in
            # stall). A fresh turn re-sets each flag via its own start frame.
            st.bot_speaking = False
            st.llm_in_flight = False
            st.fn_pending = 0
        elif isinstance(frame, BotStartedSpeakingFrame):
            st.bot_speaking = True
            st.expecting_run = False  # the awaited output has begun
            if self._on_bot_started_speaking is not None:
                self._on_bot_started_speaking()
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
            if self._on_user_started_speaking is not None:
                self._on_user_started_speaking()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            st.user_speaking = False
            st.last_user_ts = asyncio.get_running_loop().time()
        elif isinstance(frame, TTSTextFrame):
            # This tap sits after transport.output(), so TTS text arrives in sync
            # with playback — it reflects what the audience has actually heard.
            st.spoken_text = f"{st.spoken_text} {frame.text}".strip() if st.spoken_text else frame.text
        await self.push_frame(frame, direction)


class _CaptionSyncProcessor(FrameProcessor):
    """Sits between tts and transport.output(). Both TTS adapters (Azure via
    native word-boundary events, Cartesia natively) are WordTTSService
    subclasses: each spoken word arrives as its own TTSTextFrame, stamped with
    `frame.pts` = (wall-clock time synthesis reached this sentence's first
    audio) + (the word's offset within that sentence's own audio). Synthesis
    runs several times faster than playback, so on a multi-sentence turn each
    sentence's wall-clock anchor lands earlier and earlier relative to when
    its audio is actually heard — frame.pts drifts arbitrarily far ahead the
    longer the narration runs and can't be used directly for caption timing
    (nor can the client reconstruct it after the fact from packet arrival —
    the transport's playback buffer depth grows unboundedly under network
    jitter, so "when did this arrive" doesn't track "when is this heard").

    The fix: recover each word's offset *within its own sentence's audio* by
    subtracting that sentence's first word's pts (the wall-clock anchor is
    constant per sentence, so it cancels out) and send that pts_offset to the
    browser explicitly, tagged with an utterance_id per sentence. The browser
    anchors once per utterance — immediately if the bot is already speaking
    (this sentence queues straight after the previous one), or at
    BotStartedSpeaking if it's the first sentence of a fresh turn — then
    reveals each word with a plain setTimeout(pts_offset - elapsed). One
    ground-truth number per word, one clock per utterance, no continuous
    playback-position inference required.

    The original TTSTextFrame still has its pts cleared and is forwarded
    unchanged: downstream taps (_ActivityWatcher's spoken_text, the assistant
    transcript) still need it to arrive in sync with the audio it describes,
    which is unrelated to what gets shown as a caption."""

    def __init__(self) -> None:
        super().__init__()
        self._utterance_id = 0
        self._sentence_base_pts: int | None = None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSStartedFrame):
            self._utterance_id += 1
            self._sentence_base_pts = None
        elif isinstance(frame, TTSTextFrame):
            if self._sentence_base_pts is None:
                self._sentence_base_pts = frame.pts or 0
            offset_secs = ((frame.pts or 0) - self._sentence_base_pts) / 1_000_000_000
            await self.push_frame(
                RTVIServerMessageFrame(
                    data={
                        "message_type": "caption_word",
                        "text": frame.text,
                        "pts_offset": round(offset_secs, 4),
                        "utterance_id": self._utterance_id,
                    }
                ),
                direction,
            )
            frame.pts = None
        await self.push_frame(frame, direction)


class _GenTextTap(FrameProcessor):
    """Sits right after the LLM, so it sees the full generated text of the current
    turn as it streams — ahead of playback. A barge-in diffs this against the
    playback-aligned spoken_text to recover the sentences the audience never heard."""

    def __init__(self, state: _PresenterState):
        super().__init__()
        self._state = state

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        st = self._state
        if isinstance(frame, LLMFullResponseStartFrame):
            st.gen_text = ""
        elif isinstance(frame, LLMTextFrame):
            st.gen_text = f"{st.gen_text}{frame.text}" if st.gen_text else frame.text
        await self.push_frame(frame, direction)


class VoiceSessionRunner:
    def __init__(
        self,
        *,
        stt: STTFactory,
        tts: TTSFactory,
        llm: LLMFactory,
        qa_extractor: QAExtractor,
        repo: DeckRepo,
        sessions: SessionLog,
        manager: ConnectionManager,
        latency: LatencyLogger,
        always_show_slide_image: bool = False,
        tts_error_explainer: Callable[[str], str] | None = None,
    ):
        self._stt = stt
        self._tts = tts
        self._llm = llm
        self._qa_extractor = qa_extractor
        self._repo = repo
        self._sessions = sessions
        self._manager = manager
        self._latency = latency
        self._always_show_slide_image = always_show_slide_image
        self._tts_error_explainer = tts_error_explainer

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

        # ALL presentation position/flow state — current slide, linear progress,
        # coverage, autopilot, interruption tracking, pause buffer, Q&A pairing —
        # lives in this one object; see app/voice/state.py for the ownership
        # model. `state` below is pipeline liveness only. The kickoff cue lets
        # the model decide whether slide 1 is cover/title material to fold into
        # the opening (go_to_slide reason="navigation" advances past it) or real
        # content, presented normally afterward as the first slide.
        ps = PresentationState(slide_total)
        state = _PresenterState()

        def on_user_speech_start() -> None:
            remainder = ps.user_speech_started(
                gen_text=state.gen_text, spoken_text=state.spoken_text
            )
            if remainder:
                logger.info(
                    f"Narration of slide {ps.interrupted_slide} interrupted; "
                    f"{len(remainder)} chars unheard"
                )

        # --- Transport: audio in/out over the WebSocket, Silero VAD for barge-in ---
        # confidence/start_secs are deliberately conservative: barge-in must be
        # real speech, not a brief noise blip or speaker echo (a bot whose own
        # voice trips the mic will interrupt itself in a loop — hence the
        # headphones tip in the UI). Higher confidence + a longer sustained-speech
        # window filter those out at the cost of a slightly less twitchy interrupt.
        vad = SileroVADAnalyzer(
            params=VADParams(confidence=0.8, start_secs=0.3, stop_secs=0.8, min_volume=0.6)
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
                },
                "reason": {
                    "type": "string",
                    "enum": ["answer_question", "navigation"],
                    "description": (
                        "Why you are switching. 'answer_question': a detour to answer "
                        "an audience question — the talk returns to its own flow "
                        "afterwards. 'navigation': the audience explicitly asked to "
                        "move (next, previous, skip to a topic) — the talk continues "
                        "forward from the new slide."
                    ),
                },
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
        # The exact markup this TTS backend renders as a spoken pause, or None if
        # it has none — the one place this session asks the injected TTS
        # factory for that capability, never by vendor name. build_system_prompt
        # only teaches the model to use it when it's real markup this backend
        # will actually honor.
        pause_tag_example = self._tts.pause_tag(_BEAT_PAUSE_EXAMPLE_SECS)
        context = LLMContext(
            messages=[
                {
                    "role": "system",
                    "content": build_system_prompt(
                        deck,
                        always_show_slide_image=self._always_show_slide_image,
                        pause_tag_example=pause_tag_example,
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

        # Records real spoken turns (not tool-call plumbing or image blobs), each
        # tagged with whichever slide was on screen when it happened — this is
        # what actually gets persisted for debugging, not raw LLM context.
        transcript = TranscriptProcessor()

        @transcript.event_handler("on_transcript_update")
        async def on_transcript_update(_processor, frame) -> None:
            for msg in frame.messages:
                # Snapshot the conversation BEFORE appending this turn, so a
                # question's context is what preceded it, not itself.
                history = _recent_turns(turns)
                turns.append(
                    {
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": msg.timestamp,
                        "slide": ps.current_slide,
                    }
                )
                # A spoken user turn is always real audience speech (cues go
                # straight into context, never through STT). The assistant turn
                # that follows a pending question is its answer.
                if msg.role == "user":
                    qa_cancel_settle()
                    if ps.qa_answer_started:
                        await qa_flush_ready()
                    ps.qa_open(msg.content, ps.current_slide, history)
                elif msg.role == "assistant":
                    if ps.qa_record_answer_turn(msg.content, ps.current_slide) is not None:
                        qa_schedule_settle()

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                transcript.user(),
                aggregator.user(),
                rtvi,
                llm,
                _GenTextTap(state),  # full generated text, ahead of playback
                SentenceAggregator(),  # smoother TTS: send whole sentences, not fragments
                tts,
                _CaptionSyncProcessor(),  # explicit per-word pts_offset, sent to the browser
                transport.output(),
                _ActivityWatcher(
                    state,
                    on_llm_response_start=expire_temporary_slide_image,
                    on_bot_started_speaking=ps.qa_on_bot_speech_start,
                    on_user_started_speaking=on_user_speech_start,
                ),  # feeds the autopilot watchdog + Q&A pairing
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
        tts_failed = {"seen": False}

        async def cancel_after_tts_error() -> None:
            await asyncio.sleep(0.1)
            await task.cancel()

        @tts.event_handler("on_connection_error")
        async def on_tts_connection_error(_tts, error: str) -> None:
            if tts_failed["seen"]:
                return
            tts_failed["seen"] = True
            ps.autopilot_on = False
            message = (
                self._tts_error_explainer(error)
                if self._tts_error_explainer is not None
                else f"Speech connection failed: {error}"
            )
            logger.error(f"TTS connection error: {message}")
            await task.queue_frame(
                RTVIServerMessageFrame(
                    data={
                        "message_type": "session_error",
                        "code": "tts_connection_failed",
                        "message": message,
                    }
                )
            )
            asyncio.create_task(cancel_after_tts_error())

        # --- Q&A extraction runs off the voice path; entries push when ready ---
        qa_tasks: set[asyncio.Task] = set()
        qa_settle_task: asyncio.Task | None = None

        async def process_qa(
            question: str,
            answer: str,
            ask_slide: int,
            answer_slide: int,
            history: list[dict],
        ) -> None:
            ask_slide_title = ""
            if 1 <= ask_slide <= slide_total:
                ask_slide_title = deck.slides[ask_slide - 1].title
            answer_slide_title = ""
            if 1 <= answer_slide <= slide_total:
                answer_slide_title = deck.slides[answer_slide - 1].title
            try:
                result = await self._qa_extractor.extract(
                    utterance=question,
                    reply=answer,
                    deck_title=deck.title,
                    ask_slide_number=ask_slide,
                    ask_slide_title=ask_slide_title,
                    answer_slide_number=answer_slide,
                    answer_slide_title=answer_slide_title,
                    history=history,
                )
            except Exception:
                logger.exception("Q&A extraction failed")
                return
            if result is None:
                logger.info(f"Q&A skipped (not a genuine question): {question!r}")
                return
            entry = {
                "question": result.question,
                "answer": result.answer,
                "askedSlide": ask_slide,
                "answeredSlide": answer_slide,
                "timestamp": time_now_iso8601(),
            }
            events.append({"type": "qa_logged", **entry})
            await task.queue_frame(
                RTVIServerMessageFrame(data={"message_type": "qa_logged", "entry": entry})
            )
            logger.info(f"Q&A logged: {result.question!r} (slide {ask_slide}->{answer_slide})")

        async def qa_flush_ready() -> None:
            pending = ps.qa_take_ready()
            if pending is None:
                return
            answer_slide = pending.get("answer_slide") or pending["ask_slide"]
            t = asyncio.create_task(
                process_qa(
                    pending["question"],
                    pending["answer"],
                    pending["ask_slide"],
                    answer_slide,
                    pending.get("history", []),
                )
            )
            qa_tasks.add(t)
            t.add_done_callback(qa_tasks.discard)

        def qa_cancel_settle() -> None:
            nonlocal qa_settle_task
            if qa_settle_task is not None:
                qa_settle_task.cancel()
                qa_settle_task = None

        def qa_schedule_settle() -> None:
            nonlocal qa_settle_task
            qa_cancel_settle()

            async def _settle_after_delay() -> None:
                nonlocal qa_settle_task
                try:
                    await asyncio.sleep(_QA_SETTLE_SECS)
                    await qa_flush_ready()
                except asyncio.CancelledError:
                    return
                finally:
                    qa_settle_task = None

            qa_settle_task = asyncio.create_task(_settle_after_delay(), name="qa-settle")

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
            reason = str(params.arguments.get("reason", "") or "").lower()
            ps.show_slide(n, navigation=reason == "navigation")
            events.append(
                {
                    "type": "slide_change",
                    "slide": n,
                    "title": slide.title,
                    "reason": reason or "unspecified",
                    "timestamp": time_now_iso8601(),
                }
            )
            logger.info(f"go_to_slide -> {n} ({slide.title}) reason={reason or 'unspecified'}")

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
                n = resolve_slide_number(requested, default=ps.current_slide)
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
            ps.autopilot_on = action == "resume"
            events.append({"type": f"presentation_{action}", "timestamp": time_now_iso8601()})
            logger.info(f"Presentation flow: {action}")
            await params.result_callback({"ok": True, "presentation": action})

        llm.register_function("go_to_slide", handle_go_to_slide)
        llm.register_function("set_presentation_flow", handle_set_presentation_flow)
        if not self._always_show_slide_image:
            llm.register_function("look_at_slide", handle_look_at_slide)

        # --- Stage directions: append a cue to the context and trigger a run ---
        async def send_cue(text: str) -> None:
            qa_cancel_settle()
            await qa_flush_ready()
            ps.qa_reset()  # a stage cue drives autopilot narration, never an answer
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
        async def do_pause() -> None:
            resume_text = ps.capture_pause(
                bot_speaking=state.bot_speaking,
                full_text=_last_assistant_text(context.messages),
                spoken_text=state.spoken_text,
            )
            await rtvi.interrupt_bot()  # cut the audio mid-sentence, right now
            events.append({"type": "presentation_pause", "timestamp": time_now_iso8601()})
            logger.info(f"Presentation paused (replay buffer: {len(resume_text)} chars)")

        async def do_resume() -> None:
            resume_text = ps.take_resume_text()
            events.append({"type": "presentation_resume", "timestamp": time_now_iso8601()})
            if resume_text:
                # Re-speak the interrupted sentence (and the rest of that turn)
                # verbatim via TTS — no LLM round-trip, so the words match exactly
                # what the audience was hearing. expect_run() marks the pipeline
                # busy until this speech actually starts, so re-arming autopilot
                # below can't race the watchdog into advancing over the replay.
                state.expect_run()
                await task.queue_frame(TTSSpeakFrame(resume_text))
                logger.info(f"Presentation resumed; replaying {len(resume_text)} chars")
            else:
                logger.info("Presentation resumed; nothing buffered to replay")
            ps.autopilot_on = True

        @rtvi.event_handler("on_client_message")
        async def on_client_message(rtvi_proc, message) -> None:
            # The audience flipped the slide by hand in the browser: mirror it
            # so slide tags and image lookups stay truthful server-side (the
            # model separately gets a hidden text note from the browser).
            ms_type, ms_data = _parse_client_message(message, "manual-slide")
            if ms_type == "manual-slide":
                try:
                    ps.sync_manual_slide(int(ms_data.get("slide")))
                except (TypeError, ValueError):
                    await rtvi_proc.send_error_response(
                        message, f"Bad slide: {ms_data.get('slide')!r}"
                    )
                    return
                events.append(
                    {
                        "type": "manual_slide",
                        "slide": ps.current_slide,
                        "timestamp": time_now_iso8601(),
                    }
                )
                await rtvi_proc.send_server_response(message, {"ok": True})
                return

            message_type, data = _parse_client_message(message, "presentation-flow")
            if message_type != "presentation-flow":
                return
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
            await send_cue(build_kickoff_cue())
            ps.autopilot_on = True

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
                if not ps.autopilot_on or state.busy():
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
                # A slide whose narration was cut off gets finished before the
                # talk moves on — otherwise the audience permanently misses the
                # rest of it (or the model restarts it from the top).
                finish = ps.take_interrupted()
                if finish is not None:
                    n, remainder = finish
                    returning = ps.take_returning_from_qa_detour()
                    events.append(
                        {"type": "auto_finish", "slide": n, "timestamp": time_now_iso8601()}
                    )
                    await send_cue(
                        build_finish_cue(
                            n,
                            remainder,
                            returning_from_detour=returning,
                        )
                    )
                    continue
                advance = ps.advance()
                if advance is not None:
                    n, revisit = advance
                    returning = ps.take_returning_from_qa_detour()
                    events.append(
                        {
                            "type": "auto_advance",
                            "slide": n,
                            "revisit": revisit,
                            "timestamp": time_now_iso8601(),
                        }
                    )
                    await send_cue(
                        build_advance_cue(
                            n,
                            already_covered=revisit,
                            returning_from_detour=returning,
                        )
                    )
                elif ps.begin_closing():
                    events.append({"type": "closing", "timestamp": time_now_iso8601()})
                    await send_cue(build_closing_cue())

        watchdog = asyncio.create_task(autopilot_loop(), name="autopilot-watchdog")

        runner = PipelineRunner(handle_sigint=False)
        try:
            await runner.run(task)
        finally:
            watchdog.cancel()
            qa_cancel_settle()
            await qa_flush_ready()
            if qa_tasks:
                _done, pending = await asyncio.wait(list(qa_tasks), timeout=3.0)
                for t in pending:
                    t.cancel()
            try:
                await self._sessions.finish(session_id, turns, events)
            except Exception:
                logger.exception("Failed to persist session transcript")
