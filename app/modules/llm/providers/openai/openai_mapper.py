import json
from typing import Any

from app.modules.llm.providers.generation_result import (
    LLMGenerationResult,
    LLMGenerationTelemetry,
    LLMUsage,
)
from app.modules.llm.providers.llm_exceptions import LLMInvalidResponseError
from app.modules.llm.providers.prompt_envelope import PromptEnvelope


def request_payload(envelope: PromptEnvelope) -> dict[str, Any]:
    return {
        "model": envelope.model,
        "messages": [message.model_dump() for message in envelope.messages],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": envelope.output_schema_name,
                "strict": True,
                "schema": envelope.output_json_schema,
            },
        },
        **envelope.parameters,
    }


def generation_result(
    *, payload: object, envelope: PromptEnvelope, latency_ms: int, headers: Any
) -> LLMGenerationResult:
    if not isinstance(payload, dict):
        raise LLMInvalidResponseError("openai_invalid_response")
    try:
        choice = payload["choices"][0]
        message = choice["message"]
        refusal = message.get("refusal")
        content = message.get("content")
        output = None if refusal is not None else json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMInvalidResponseError("openai_malformed_structured_output") from exc
    usage = payload.get("usage", {})
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("prompt_tokens_details", {})
    details = details if isinstance(details, dict) else {}
    request_id = headers.get("x-request-id") if hasattr(headers, "get") else None
    return LLMGenerationResult(
        output=output,
        telemetry=LLMGenerationTelemetry(
            provider="openai",
            model=envelope.model,
            latency_ms=latency_ms,
            usage=LLMUsage(
                input_tokens=usage.get("prompt_tokens")
                if isinstance(usage.get("prompt_tokens"), int)
                else None,
                output_tokens=usage.get("completion_tokens")
                if isinstance(usage.get("completion_tokens"), int)
                else None,
                total_tokens=usage.get("total_tokens")
                if isinstance(usage.get("total_tokens"), int)
                else None,
                cache_read_tokens=details.get("cached_tokens", 0)
                if isinstance(details.get("cached_tokens", 0), int)
                else 0,
            ),
            correlation_id=envelope.correlation_id,
            provider_request_id=request_id if isinstance(request_id, str) else None,
            finish_reason=choice.get("finish_reason")
            if isinstance(choice.get("finish_reason"), str)
            else None,
            refusal=refusal is not None,
        ),
    )
