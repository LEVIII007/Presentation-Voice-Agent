"""Composition root — the ONLY file that knows which concrete adapters are
wired in. A vendor swap (Deepgram -> something else, SQLite -> Postgres,
local disk -> S3) is a change here, not in the services."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from ..adapters.blob import LocalBlobStore
from ..adapters.db import make_engine
from ..adapters.narration import AzureNarrationModel
from ..adapters.pipecat_services import (
    AzureLLMFactory,
    CartesiaTTSFactory,
    DeepgramSTTFactory,
)
from ..adapters.qa_log import AzureQAExtractor
from ..adapters.renderer import PyMuPDFRenderer
from ..adapters.repos import SqlDeckRepo, SqlSessionLog
from ..services.ingestion import IngestionService
from ..services.tts_diagnostics import describe_tts_connection_error
from ..services.worker import IngestionWorker
from ..voice.manager import ConnectionManager
from ..voice.session import VoiceSessionRunner
from .settings import Settings
from .telemetry import LatencyLogger


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    repo: SqlDeckRepo
    sessions: SqlSessionLog
    blobs: LocalBlobStore
    renderer: PyMuPDFRenderer
    narrator: AzureNarrationModel
    ingestion: IngestionService
    worker: IngestionWorker
    manager: ConnectionManager
    latency: LatencyLogger
    session_runner: VoiceSessionRunner


async def build_container(settings: Settings) -> Container:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine, session_factory = await make_engine(settings.resolved_database_url)

    repo = SqlDeckRepo(session_factory)
    sessions = SqlSessionLog(session_factory)
    blobs = LocalBlobStore(settings.data_dir)
    renderer = PyMuPDFRenderer(soffice_path=settings.soffice_path)
    narrator = AzureNarrationModel(
        api_key=settings.azure_openai_api_key,
        endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
        deployment=settings.azure_openai_chat_deployment,
    )
    ingestion = IngestionService(repo, blobs, renderer, narrator, settings)
    worker = IngestionWorker(ingestion, repo)
    manager = ConnectionManager(max_sessions=settings.max_sessions)
    latency = LatencyLogger(
        enabled=settings.latency_log,
        file_path=settings.data_dir / settings.latency_log_file,
    )
    if settings.tts_provider == "azure":
        # Lazy import: only load the Azure speech SDK when actually selected.
        from ..adapters.azure_tts import AzureTTSFactory

        tts = AzureTTSFactory(
            api_key=settings.azure_speech_key,
            region=settings.azure_speech_region,
            voice=settings.azure_speech_voice,
        )
    else:
        tts = CartesiaTTSFactory(settings.cartesia_api_key, settings.cartesia_voice_id)
    session_runner = VoiceSessionRunner(
        stt=DeepgramSTTFactory(settings.deepgram_api_key),
        tts=tts,
        llm=AzureLLMFactory(
            api_key=settings.azure_openai_api_key,
            endpoint=settings.azure_openai_endpoint,
            deployment=settings.azure_openai_chat_deployment,
            api_version=settings.azure_openai_api_version,
        ),
        qa_extractor=AzureQAExtractor(
            api_key=settings.azure_openai_api_key,
            endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
            deployment=settings.azure_openai_chat_deployment,
        ),
        repo=repo,
        sessions=sessions,
        manager=manager,
        latency=latency,
        always_show_slide_image=settings.voice_always_show_slide_image,
        tts_error_explainer=lambda error: describe_tts_connection_error(settings, error),
    )

    return Container(
        settings=settings,
        engine=engine,
        repo=repo,
        sessions=sessions,
        blobs=blobs,
        renderer=renderer,
        narrator=narrator,
        ingestion=ingestion,
        worker=worker,
        manager=manager,
        latency=latency,
        session_runner=session_runner,
    )
