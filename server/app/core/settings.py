"""App settings. Reads the same .env the original demo used."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Vendors
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""
    cartesia_voice_id: str = "71a7ad14-091c-4e8e-a314-022ece01c121"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2025-01-01-preview"
    azure_openai_chat_deployment: str = "gpt-5-mini"

    # Server
    port: int = 7860
    public_url: str = ""  # e.g. https://demo.example.com when behind a domain
    voice_always_show_slide_image: bool = False  # rollback to eager per-turn slide images

    # Storage
    data_dir: Path = Path("data")
    database_url: str = ""  # empty -> sqlite file under data_dir; set to postgres in prod

    # Limits
    max_sessions: int = 20
    max_upload_mb: int = 40
    max_slides: int = 60
    narration_concurrency: int = 2

    # Telemetry: per-turn voice latency breakdown -> console + JSONL for analysis
    latency_log: bool = True
    latency_log_file: str = "latency.jsonl"  # relative to data_dir

    # Optional explicit path to LibreOffice for PPTX -> PDF conversion
    soffice_path: str = ""

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'app.db').as_posix()}"
