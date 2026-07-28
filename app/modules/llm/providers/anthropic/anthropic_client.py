from typing import Any

import httpx

from app.modules.llm.providers.llm_exceptions import LLMPermanentError, LLMTransientError


class AnthropicClient:
    def __init__(
        self, *, api_key: str, timeout_seconds: float, http_client: httpx.AsyncClient
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self.http_client = http_client

    async def create_message(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            response = await self.http_client.post(
                "https://api.anthropic.com/v1/messages",
                timeout=self._timeout,
                headers={"x-api-key": self._api_key, "anthropic-version": "2023-06-01"},
                json=payload,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMTransientError("anthropic_transport_failure") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise LLMTransientError("anthropic_transient_failure")
        if response.is_error:
            raise LLMPermanentError("anthropic_permanent_failure")
        return response
