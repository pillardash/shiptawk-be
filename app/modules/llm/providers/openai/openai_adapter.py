from time import monotonic

from app.modules.llm.providers.generation_result import LLMGenerationResult
from app.modules.llm.providers.openai.openai_client import OpenAIClient
from app.modules.llm.providers.openai.openai_mapper import generation_result, request_payload
from app.modules.llm.providers.prompt_envelope import PromptEnvelope


class OpenAIAdapter:
    def __init__(self, client: OpenAIClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, envelope: PromptEnvelope) -> LLMGenerationResult:
        started = monotonic()
        response = await self._client.response(request_payload(envelope))
        return generation_result(
            payload=response.json(),
            envelope=envelope,
            latency_ms=round((monotonic() - started) * 1000),
            headers=response.headers,
        )
