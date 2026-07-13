"""Pipecat service factories — the only module that knows which STT/TTS/LLM
vendors are in use. Each voice session gets fresh service instances.

AzureLLMService note: Pipecat ships one, but importing `pipecat.services.azure`
eagerly pulls in the Azure *speech* SDK (`pipecat-ai[azure]`) — a heavy dep we
don't use. This is a faithful copy: OpenAILLMService with an AsyncAzureOpenAI
client. gpt-5-mini rejects a custom temperature, so none is ever passed.
"""

from __future__ import annotations

from deepgram import LiveOptions
from openai import AsyncAzureOpenAI
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService


class AzureLLMService(OpenAILLMService):
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        model: str,  # for Azure this is the *deployment* name
        api_version: str = "2025-01-01-preview",
        **kwargs,
    ):
        # Set before super().__init__ — it calls create_client() during init.
        self._endpoint = endpoint
        self._api_version = api_version
        super().__init__(api_key=api_key, model=model, **kwargs)

    def create_client(self, api_key=None, base_url=None, **kwargs):
        return AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=self._endpoint,
            api_version=self._api_version,
        )


class DeepgramSTTFactory:
    def __init__(self, api_key: str):
        self._api_key = api_key

    def create(self) -> DeepgramSTTService:
        return DeepgramSTTService(
            api_key=self._api_key,
            live_options=LiveOptions(
                model="nova-2",
                language="en-US",
                smart_format=True,
                punctuate=True,
                vad_events=False,  # local Silero VAD owns interruption, not Deepgram
            ),
        )


class CartesiaTTSFactory:
    def __init__(self, api_key: str, voice_id: str):
        self._api_key = api_key
        self._voice_id = voice_id

    def create(self) -> CartesiaTTSService:
        return CartesiaTTSService(
            api_key=self._api_key, voice_id=self._voice_id, model="sonic-2"
        )

    def pause_tag(self, seconds: float) -> str:
        # Cartesia renders this inline SSML-like tag as an actual synthesized
        # pause (pipecat's own builtin helper for it — not our own string).
        return CartesiaTTSService.PAUSE_TAG(seconds)


class AzureLLMFactory:
    def __init__(self, *, api_key: str, endpoint: str, deployment: str, api_version: str):
        self._kwargs = dict(
            api_key=api_key, endpoint=endpoint, model=deployment, api_version=api_version
        )

    def create(self) -> AzureLLMService:
        return AzureLLMService(**self._kwargs)
