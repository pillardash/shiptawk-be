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
    system = next(
        (message.content for message in envelope.messages if message.role == "system"), ""
    )
    messages = [message.model_dump() for message in envelope.messages if message.role != "system"]
    parameters = dict(envelope.parameters)
    max_tokens = parameters.pop("max_tokens", 4096)
    return {
        "model": envelope.model,
        "system": system,
        "messages": messages,
        "max_tokens": max_tokens,
        **parameters,
    }


def generation_result(
    *, payload: object, envelope: PromptEnvelope, latency_ms: int, headers: Any
) -> LLMGenerationResult:
    if not isinstance(payload, dict):
        raise LLMInvalidResponseError("anthropic_invalid_response")
    refusal = payload.get("stop_reason") == "refusal"
    try:
        output = None if refusal else json.loads(payload["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMInvalidResponseError("anthropic_malformed_structured_output") from exc
    usage = payload.get("usage", {})
    usage = usage if isinstance(usage, dict) else {}
    return LLMGenerationResult(
        output=output,
        telemetry=LLMGenerationTelemetry(
            provider="anthropic",
            model=envelope.model,
            latency_ms=latency_ms,
            correlation_id=envelope.correlation_id,
            provider_request_id=headers.get("request-id") if hasattr(headers, "get") else None,
            finish_reason=payload.get("stop_reason")
            if isinstance(payload.get("stop_reason"), str)
            else None,
            refusal=refusal,
            usage=LLMUsage(
                input_tokens=usage.get("input_tokens")
                if isinstance(usage.get("input_tokens"), int)
                else None,
                output_tokens=usage.get("output_tokens")
                if isinstance(usage.get("output_tokens"), int)
                else None,
                total_tokens=(usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
                if isinstance(usage.get("input_tokens", 0), int)
                and isinstance(usage.get("output_tokens", 0), int)
                else None,
                cache_read_tokens=usage.get("cache_read_input_tokens", 0)
                if isinstance(usage.get("cache_read_input_tokens", 0), int)
                else 0,
                cache_write_tokens=usage.get("cache_creation_input_tokens", 0)
                if isinstance(usage.get("cache_creation_input_tokens", 0), int)
                else 0,
            ),
        ),
    )
