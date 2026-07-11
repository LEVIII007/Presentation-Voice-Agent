"""Shared role mapping for latency telemetry."""

from __future__ import annotations

ROLE_ORDER = ("stt", "llm", "tts")
ROLE_META = {
    "stt": {
        "label": "STT",
        "processor_prefixes": (
            "DeepgramSTTService",
        ),
    },
    "llm": {
        "label": "LLM",
        "processor_prefixes": (
            "AzureLLMService",
        ),
    },
    "tts": {
        "label": "TTS",
        "processor_prefixes": (
            "CartesiaTTSService",
            "AzureV2TTSService",
            "AzureTTSService",
        ),
    },
}


def infer_latency_role(processor: str | None) -> str | None:
    name = str(processor or "")
    for role in ROLE_ORDER:
        prefixes = ROLE_META[role]["processor_prefixes"]
        if any(name.startswith(prefix) for prefix in prefixes):
            return role
    return None
