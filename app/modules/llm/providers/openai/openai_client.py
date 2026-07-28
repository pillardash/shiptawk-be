from typing import Any

import httpx

from app.modules.llm.providers.llm_exceptions import (
    LLMPermanentError,
    LLMTransientError,
)


class OpenAIClient:
    def __init__(
        self, *, api_key: str, timeout_seconds: float, http_client: httpx.AsyncClient
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self.http_client = http_client

    async def chat_completion(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            response = await self.http_client.post(
                "https://api.openai.com/v1/chat/completions",
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMTransientError("openai_transport_failure") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise LLMTransientError("openai_transient_failure")
        if response.is_error:
            raise LLMPermanentError("openai_permanent_failure")
        return response
