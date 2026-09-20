import logging
from typing import Any

import httpx

from app.modules.llm.providers.llm_exceptions import (
    LLMPermanentError,
    LLMTransientError,
)

logger = logging.getLogger(__name__)


def _safe_error_details(response: httpx.Response) -> dict[str, object]:
    details: dict[str, object] = {
        "status": response.status_code,
        "provider_request_id": response.headers.get("x-request-id"),
    }
    try:
        payload = response.json()
    except ValueError:
        return details
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return details
    error = payload["error"]
    for key in ("type", "code", "param"):
        if isinstance(error.get(key), str):
            details[f"provider_{key}"] = error[key]
    if isinstance(error.get("message"), str):
        details["provider_message"] = error["message"][:500]
    return details


def _log_provider_failure(message: str, response: httpx.Response) -> None:
    details = _safe_error_details(response)
    logger.warning(
        "%s status=%s provider_request_id=%s type=%s code=%s param=%s message=%s",
        message,
        details.get("status"),
        details.get("provider_request_id"),
        details.get("provider_type"),
        details.get("provider_code"),
        details.get("provider_param"),
        details.get("provider_message"),
    )


class OpenAIClient:
    def __init__(
        self, *, api_key: str, timeout_seconds: float, http_client: httpx.AsyncClient
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds
        self.http_client = http_client

    async def response(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            response = await self.http_client.post(
                "https://api.openai.com/v1/responses",
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMTransientError("openai_transport_failure") from exc
        if response.status_code == 429 or response.status_code >= 500:
            _log_provider_failure("OpenAI transient request failure", response)
            raise LLMTransientError("openai_transient_failure")
        if response.is_error:
            _log_provider_failure("OpenAI permanent request failure", response)
            raise LLMPermanentError("openai_permanent_failure")
        return response
