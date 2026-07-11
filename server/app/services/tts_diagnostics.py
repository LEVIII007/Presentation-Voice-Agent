"""TTS provider probes + user-facing diagnostics."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import quote

import websockets
from websockets.exceptions import InvalidStatus

from ..core.settings import Settings

_CARTESIA_VERSION = "2025-04-16"
_CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket"


@dataclass(frozen=True)
class TTSProbeResult:
    ok: bool
    provider: str
    message: str = ""


def _provider_name(settings: Settings) -> str:
    provider = (settings.tts_provider or "cartesia").strip().lower()
    if provider == "azure":
        return "Azure Speech"
    if provider == "cartesia":
        return "Cartesia"
    return provider or "TTS"


def _azure_switch_hint(settings: Settings) -> str:
    if settings.azure_speech_key:
        return (
            " If you want to use Azure Speech instead, set `TTS_PROVIDER=azure` "
            "and confirm `AZURE_SPEECH_REGION` matches that Azure Speech resource."
        )
    return (
        " To switch providers, set `TTS_PROVIDER=azure` and add "
        "`AZURE_SPEECH_KEY` plus `AZURE_SPEECH_REGION`."
    )


def describe_tts_connection_error(settings: Settings, raw_error: str) -> str:
    """Turn a low-level provider failure into a short, actionable message."""
    provider = (settings.tts_provider or "cartesia").strip().lower()
    error = (raw_error or "unknown connection error").strip()
    error_lower = error.lower()

    if provider == "cartesia":
        if "http 402" in error_lower:
            return (
                "Cartesia rejected the speech connection with HTTP 402. "
                "The app is reaching Cartesia, but that account is declining this session. "
                "Check Cartesia billing or credits for the current API key."
                f"{_azure_switch_hint(settings)}"
            )
        if "http 401" in error_lower:
            return (
                "Cartesia rejected the speech connection with HTTP 401. "
                "Check `CARTESIA_API_KEY`."
            )
        return f"Cartesia speech connection failed: {error}.{_azure_switch_hint(settings)}"

    if provider == "azure":
        return (
            "Azure Speech connection failed. Confirm `AZURE_SPEECH_KEY`, "
            "`AZURE_SPEECH_REGION`, and the selected voice name. "
            f"Raw error: {error}"
        )

    return f"{_provider_name(settings)} speech connection failed: {error}"


async def probe_tts_provider(settings: Settings) -> TTSProbeResult:
    """Best-effort readiness check for the configured TTS provider."""
    provider = (settings.tts_provider or "cartesia").strip().lower()

    if provider == "azure":
        missing: list[str] = []
        if not settings.azure_speech_key:
            missing.append("AZURE_SPEECH_KEY")
        if not settings.azure_speech_region:
            missing.append("AZURE_SPEECH_REGION")
        if missing:
            joined = ", ".join(f"`{name}`" for name in missing)
            return TTSProbeResult(
                ok=False,
                provider="azure",
                message=f"Azure Speech is selected, but {joined} is missing.",
            )
        return TTSProbeResult(ok=True, provider="azure")

    if provider != "cartesia":
        return TTSProbeResult(ok=True, provider=provider)

    if not settings.cartesia_api_key:
        return TTSProbeResult(
            ok=False,
            provider="cartesia",
            message=(
                "Cartesia is selected, but `CARTESIA_API_KEY` is empty."
                f"{_azure_switch_hint(settings)}"
            ),
        )

    url = (
        f"{_CARTESIA_WS_URL}"
        f"?api_key={quote(settings.cartesia_api_key, safe='')}"
        f"&cartesia_version={_CARTESIA_VERSION}"
    )
    websocket = None
    try:
        websocket = await asyncio.wait_for(websockets.connect(url, open_timeout=5), timeout=6)
        return TTSProbeResult(ok=True, provider="cartesia")
    except InvalidStatus as exc:
        return TTSProbeResult(
            ok=False,
            provider="cartesia",
            message=describe_tts_connection_error(settings, str(exc)),
        )
    except Exception as exc:
        return TTSProbeResult(
            ok=False,
            provider="cartesia",
            message=describe_tts_connection_error(settings, str(exc)),
        )
    finally:
        if websocket is not None:
            await websocket.close()
