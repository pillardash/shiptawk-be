import json
from time import monotonic

import httpx

from app.domains.generation.models import (
    GenerationRequest,
    GenerationTelemetry,
    ProviderGenerationResult,
    StructuredGenerationOutput,
)
from app.domains.generation.provider import ProviderPermanentError, ProviderTransientError


class OpenAIHTTPGenerationProvider:
    """OpenAI transport adapter; domain orchestration remains provider-neutral."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: GenerationRequest) -> ProviderGenerationResult:
        started = monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": request.model,
                        "messages": [
                            {"role": "system", "content": request.system_instructions},
                            {"role": "user", "content": request.input_text},
                        ],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "tweet_candidates",
                                "strict": True,
                                "schema": StructuredGenerationOutput.model_json_schema(),
                            },
                        },
                        **request.parameters,
                    },
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderTransientError("openai_transport_failure") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderTransientError("openai_transient_failure")
        if response.is_error:
            raise ProviderPermanentError("openai_permanent_failure")
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
            output = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("OpenAI returned malformed structured output.") from exc
        usage = payload.get("usage", {})
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        return ProviderGenerationResult(
            output=output,
            telemetry=GenerationTelemetry(
                provider=self.name,
                model=request.model,
                latency_ms=round((monotonic() - started) * 1000),
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                total_tokens=(
                    usage.get("total_tokens")
                    if isinstance(usage.get("total_tokens"), int)
                    else None
                ),
                correlation_id=request.correlation_id,
            ),
        )
