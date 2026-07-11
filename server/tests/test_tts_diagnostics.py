import asyncio

from app.core.settings import Settings
from app.services.tts_diagnostics import describe_tts_connection_error, probe_tts_provider


def test_describe_cartesia_402_mentions_billing_and_azure_switch():
    settings = Settings(
        _env_file=None,
        tts_provider="cartesia",
        cartesia_api_key="cartesia-key",
        azure_speech_key="azure-key",
    )

    message = describe_tts_connection_error(
        settings, "server rejected WebSocket connection: HTTP 402"
    )

    assert "HTTP 402" in message
    assert "billing or credits" in message
    assert "TTS_PROVIDER=azure" in message


def test_probe_cartesia_without_key_fails_fast():
    settings = Settings(_env_file=None, tts_provider="cartesia", cartesia_api_key="")

    result = asyncio.run(probe_tts_provider(settings))

    assert result.ok is False
    assert result.provider == "cartesia"
    assert "CARTESIA_API_KEY" in result.message


def test_probe_azure_without_region_reports_missing_setting():
    settings = Settings(
        _env_file=None,
        tts_provider="azure",
        azure_speech_key="azure-key",
        azure_speech_region="",
    )

    result = asyncio.run(probe_tts_provider(settings))

    assert result.ok is False
    assert result.provider == "azure"
    assert "AZURE_SPEECH_REGION" in result.message
