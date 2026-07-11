"""Voice pipeline: Deepgram STT -> Groq LLM -> Cartesia TTS over a WebSocket.

One VoicePresenter is created per WebSocket connection. It:
- runs a Pipecat cascade with barge-in (interruption) enabled,
- exposes a single `go_to_slide` tool to the LLM,
- pushes slide changes to the browser as RTVI server messages,
- greets the audience and shows slide 1 on connect.
"""

import asyncio
import os

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
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from deepgram import LiveOptions

from azure_llm import AzureLLMService
from slides import SLIDE_COUNT, SLIDES, build_system_prompt


def _slide_change_frame(slide_number: int) -> RTVIServerMessageFrame:
    """Build the RTVI message the browser listens for to switch slides."""
    return RTVIServerMessageFrame(
        data={"message_type": "go_to_slide", "slide": slide_number}
    )


async def run_bot(websocket) -> None:
    """Assemble and run the voice pipeline for a single connection."""

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

    # --- Services ---
    stt = DeepgramSTTService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        live_options=LiveOptions(
            model="nova-3",
            language="en-US",
            smart_format=True,
            punctuate=True,
            vad_events=False,  # local Silero VAD owns interruption, not Deepgram
        ),
    )
    tts = CartesiaTTSService(
        api_key=os.environ["CARTESIA_API_KEY"],
        voice_id=os.environ.get("CARTESIA_VOICE_ID", "71a7ad14-091c-4e8e-a314-022ece01c121"),
        model="sonic-2",
    )
    llm = AzureLLMService(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        model=os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5-mini"),
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
    )

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
                "description": f"The slide to show, from 1 to {SLIDE_COUNT}.",
            }
        },
        required=["slide_number"],
    )
    context = LLMContext(
        messages=[{"role": "system", "content": build_system_prompt()}],
        tools=ToolsSchema(standard_tools=[go_to_slide_schema]),
    )
    aggregator = LLMContextAggregatorPair(context)

    rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

    # Mute STT only while the opening greeting plays, so the bot can't interrupt
    # itself before the user has said anything. After that, barge-in is fully live.
    stt_mute = STTMuteFilter(
        config=STTMuteConfig(strategies={STTMuteStrategy.MUTE_UNTIL_FIRST_BOT_COMPLETE})
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            stt_mute,
            aggregator.user(),
            rtvi,
            llm,
            SentenceAggregator(),  # smoother TTS: send whole sentences, not fragments
            tts,
            transport.output(),
            aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,  # barge-in: user speech stops the bot immediately
            enable_metrics=True,
        ),
        observers=[RTVIObserver(rtvi)],
    )

    # --- Tool handler: push the slide change to the browser, feed notes back to the LLM ---
    async def handle_go_to_slide(params: FunctionCallParams) -> None:
        requested = params.arguments.get("slide_number")
        try:
            n = int(requested)
        except (TypeError, ValueError):
            await params.result_callback({"error": f"Invalid slide number: {requested!r}"})
            return
        n = max(1, min(SLIDE_COUNT, n))  # clamp into range

        await task.queue_frame(_slide_change_frame(n))
        logger.info(f"go_to_slide -> {n} ({SLIDES[n - 1]['title']})")

        slide = SLIDES[n - 1]
        await params.result_callback(
            {
                "slide_number": n,
                "title": slide["title"],
                "notes": slide["notes"],
                "instruction": "You are now on this slide. Answer using these notes, briefly.",
            }
        )

    llm.register_function("go_to_slide", handle_go_to_slide)

    # --- On connect: show slide 1 and greet ---
    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        await asyncio.sleep(1.5)  # let the pipeline's StartFrame settle before pushing
        await task.queue_frame(_slide_change_frame(1))
        greeting = (
            "Hi, and welcome to Electric Vehicles 101. I'm your presenter, and I'll walk "
            "you through how EVs work, batteries, charging, and cost. Jump in with a "
            "question any time and I'll bring up the right slide. Ready when you are! "
        )
        await task.queue_frame(LLMFullResponseStartFrame())
        await task.queue_frame(TextFrame(greeting))
        await task.queue_frame(LLMFullResponseEndFrame())
        context.messages.append({"role": "assistant", "content": greeting})

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Client disconnected; cancelling task")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
