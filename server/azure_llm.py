"""Azure OpenAI LLM service.

Pipecat ships an `AzureLLMService`, but importing `pipecat.services.azure`
eagerly pulls in the Azure *speech* SDK (`pipecat-ai[azure]`) — a heavy dep we
don't use (our STT/TTS are Deepgram/Cartesia). This is a faithful copy of that
class: it just extends OpenAILLMService and swaps the client for AsyncAzureOpenAI.

gpt-5-mini note: we deliberately pass no temperature/max_tokens. Pipecat defaults
both to NOT_GIVEN (omitted from the request), which is exactly what gpt-5-mini
requires — it rejects a custom temperature and only accepts max_completion_tokens.
"""

from openai import AsyncAzureOpenAI
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
