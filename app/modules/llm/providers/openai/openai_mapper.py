import json
from copy import deepcopy
from typing import Any

from app.modules.llm.providers.generation_result import (
    LLMGenerationResult,
    LLMGenerationTelemetry,
    LLMUsage,
)
from app.modules.llm.providers.llm_exceptions import LLMInvalidResponseError
from app.modules.llm.providers.prompt_envelope import PromptEnvelope


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(schema)

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                properties = node.get("properties")
                node["additionalProperties"] = False
                if isinstance(properties, dict):
                    node["required"] = list(properties)
                    for property_schema in properties.values():
                        visit(property_schema)
                else:
                    node["required"] = []
            for key in ("$defs", "definitions"):
                definitions = node.get(key)
                if isinstance(definitions, dict):
                    for definition in definitions.values():
                        visit(definition)
            items = node.get("items")
            if items is not None:
                visit(items)
            for key in ("anyOf", "oneOf", "allOf"):
                branches = node.get(key)
                if isinstance(branches, list):
                    for branch in branches:
                        visit(branch)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(normalized)
    return normalized


def request_payload(envelope: PromptEnvelope) -> dict[str, Any]:
    return {
        "model": envelope.model,
        "input": [message.model_dump() for message in envelope.messages],
        "text": {
            "format": {
                "type": "json_schema",
                "name": envelope.output_schema_name,
                "strict": True,
                "schema": strict_schema(envelope.output_json_schema),
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
        output_items = payload["output"]
        refusal = next(
            (
                part.get("refusal")
                for item in output_items
                if isinstance(item, dict)
                for part in item.get("content", [])
                if isinstance(part, dict) and isinstance(part.get("refusal"), str)
            ),
            None,
        )
        content = next(
            (
                part.get("text")
                for item in output_items
                if isinstance(item, dict)
                for part in item.get("content", [])
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ),
            None,
        )
        if refusal is None and not isinstance(content, str):
            raise LLMInvalidResponseError("openai_malformed_structured_output")
        output = None if refusal is not None else json.loads(str(content))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMInvalidResponseError("openai_malformed_structured_output") from exc
    usage = payload.get("usage", {})
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("input_tokens_details", {})
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
            finish_reason=payload.get("status") if isinstance(payload.get("status"), str) else None,
            refusal=refusal is not None,
        ),
    )
