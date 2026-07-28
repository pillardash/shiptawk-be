from time import monotonic

from app.modules.llm.providers.anthropic.anthropic_client import AnthropicClient
from app.modules.llm.providers.anthropic.anthropic_mapper import generation_result, request_payload
from app.modules.llm.providers.generation_result import LLMGenerationResult
from app.modules.llm.providers.prompt_envelope import PromptEnvelope


class AnthropicAdapter:
    def __init__(self, client: AnthropicClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "anthropic"

    async def generate(self, envelope: PromptEnvelope) -> LLMGenerationResult:
        started = monotonic()
        response = await self._client.create_message(request_payload(envelope))
        return generation_result(
            payload=response.json(),
            envelope=envelope,
            latency_ms=round((monotonic() - started) * 1000),
            headers=response.headers,
        )
