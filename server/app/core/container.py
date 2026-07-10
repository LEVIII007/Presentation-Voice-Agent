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
from ..adapters.renderer import PyMuPDFRenderer
from ..adapters.repos import SqlDeckRepo, SqlSessionLog
from ..services.ingestion import IngestionService
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
    session_runner = VoiceSessionRunner(
        stt=DeepgramSTTFactory(settings.deepgram_api_key),
        tts=CartesiaTTSFactory(settings.cartesia_api_key, settings.cartesia_voice_id),
        llm=AzureLLMFactory(
            api_key=settings.azure_openai_api_key,
            endpoint=settings.azure_openai_endpoint,
            deployment=settings.azure_openai_chat_deployment,
            api_version=settings.azure_openai_api_version,
        ),
        repo=repo,
        sessions=sessions,
        manager=manager,
        latency=latency,
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
