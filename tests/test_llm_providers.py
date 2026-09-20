from decimal import Decimal

import httpx
import pytest

from app.modules.llm.policies.llm_route_policy import (
    LLMModelRegistration,
    LLMRoutePlan,
    LLMRoutePolicy,
    LLMRouteStep,
    ModelPricing,
)
from app.modules.llm.providers.anthropic.anthropic_adapter import AnthropicAdapter
from app.modules.llm.providers.anthropic.anthropic_client import AnthropicClient
from app.modules.llm.providers.anthropic.anthropic_mapper import (
    request_payload as anthropic_payload,
)
from app.modules.llm.providers.fake.fake_llm_provider import FakeLLMProvider
from app.modules.llm.providers.generation_result import LLMGenerationResult, LLMGenerationTelemetry
from app.modules.llm.providers.llm_exceptions import LLMInvalidResponseError, LLMPermanentError
from app.modules.llm.providers.openai.openai_adapter import OpenAIAdapter
from app.modules.llm.providers.openai.openai_client import OpenAIClient
from app.modules.llm.providers.openai.openai_mapper import (
    request_payload as openai_payload,
)
from app.modules.llm.providers.openai.openai_mapper import (
    strict_schema,
)
from app.modules.llm.providers.prompt_envelope import PromptEnvelope, PromptMessage
from app.modules.llm.providers.provider_registry import LLMProviderRegistry


def envelope() -> PromptEnvelope:
    return PromptEnvelope(
        model="test-model",
        prompt_id="test.prompt",
        prompt_version="test.prompt.v1",
        input_schema_version="test.input.v1",
        output_schema_version="test.output.v1",
        messages=(
            PromptMessage(role="system", content="Return JSON."),
            PromptMessage(role="user", content="Sanitized evidence."),
        ),
        output_schema_name="test_output",
        output_json_schema={"type": "object"},
        correlation_id="correlation-1",
    )


def result() -> LLMGenerationResult:
    return LLMGenerationResult(
        output={"ok": True},
        telemetry=LLMGenerationTelemetry(
            provider="fake", model="test-model", correlation_id="correlation-1"
        ),
    )


@pytest.mark.asyncio
async def test_fake_provider_preserves_domain_envelope() -> None:
    provider = FakeLLMProvider(result())

    response = await provider.generate(envelope())

    assert response.output == {"ok": True}
    assert provider.requests == [envelope()]


def test_provider_registry_rejects_unconfigured_provider() -> None:
    with pytest.raises(LLMPermanentError, match="llm_provider_not_configured"):
        LLMProviderRegistry({}).get("missing")


def test_openai_payload_uses_caller_schema() -> None:
    payload = openai_payload(envelope())

    assert payload["text"]["format"]["name"] == "test_output"
    assert payload["text"]["format"]["schema"] == {
        "type": "object",
        "additionalProperties": False,
        "required": [],
    }


def test_openai_strict_schema_normalizes_nested_objects_without_mutating_input() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"$ref": "#/$defs/Item"},
            }
        },
        "$defs": {
            "Item": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            }
        },
    }

    normalized = strict_schema(schema)

    assert normalized["additionalProperties"] is False
    assert normalized["required"] == ["items"]
    assert normalized["$defs"]["Item"]["additionalProperties"] is False
    assert normalized["$defs"]["Item"]["required"] == ["name"]
    assert "additionalProperties" not in schema


def test_anthropic_payload_does_not_mutate_parameters() -> None:
    request = envelope().model_copy(update={"parameters": {"max_tokens": 123, "temperature": 0}})

    payload = anthropic_payload(request)

    assert payload["max_tokens"] == 123
    assert request.parameters == {"max_tokens": 123, "temperature": 0}


def test_route_policy_returns_ordered_bounded_plan() -> None:
    registrations = {
        "primary": LLMModelRegistration(
            name="primary",
            provider="openai",
            model="gpt-test",
            capabilities={"structured_output"},
            pricing=ModelPricing(input_per_million=Decimal("1"), output_per_million=Decimal("2")),
            pricing_version="2026-07-01",
        ),
        "fallback": LLMModelRegistration(
            name="fallback",
            provider="anthropic",
            model="claude-test",
            capabilities={"structured_output"},
            pricing=ModelPricing(input_per_million=Decimal("3"), output_per_million=Decimal("4")),
            pricing_version="2026-07-01",
        ),
    }
    policy = LLMRoutePolicy(
        registrations,
        {
            "content": LLMRoutePlan(
                name="content",
                steps=(LLMRouteStep(model="primary"), LLMRouteStep(model="fallback")),
                max_attempts=2,
            )
        },
    )

    route = policy.resolve("content", required_capabilities={"structured_output"})

    assert [item.name for item in route.registrations] == ["primary", "fallback"]


def test_route_policy_rejects_unbounded_plan() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        LLMRoutePlan(name="bad", steps=(LLMRouteStep(model="one"),), max_attempts=2)


@pytest.mark.asyncio
async def test_openai_adapter_maps_structured_output_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            request=request,
            headers={"x-request-id": "req-1"},
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"ok": true}'}],
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "output_tokens": 3,
                    "total_tokens": 5,
                    "input_tokens_details": {"cached_tokens": 1},
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIAdapter(
        OpenAIClient(api_key="test-key", timeout_seconds=1, http_client=client)
    )
    response = await provider.generate(envelope())

    assert response.output == {"ok": True}
    assert response.telemetry.provider_request_id == "req-1"
    assert response.telemetry.usage.total_tokens == 5
    assert response.telemetry.usage.cache_read_tokens == 1
    assert response.telemetry.finish_reason == "completed"
    await client.aclose()


@pytest.mark.asyncio
async def test_anthropic_adapter_maps_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(
            200,
            request=request,
            headers={"request-id": "req-2"},
            json={
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 4,
                    "output_tokens": 2,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 1,
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicAdapter(
        AnthropicClient(api_key="test-key", timeout_seconds=1, http_client=client)
    )
    response = await provider.generate(envelope())

    assert response.output == {"ok": True}
    assert response.telemetry.provider_request_id == "req-2"
    assert response.telemetry.finish_reason == "end_turn"
    assert response.telemetry.usage.cache_write_tokens == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_anthropic_refusal_does_not_expose_provider_text() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                request=request,
                json={
                    "content": [{"type": "text", "text": "sensitive provider refusal"}],
                    "stop_reason": "refusal",
                },
            )
        )
    )
    provider = AnthropicAdapter(
        AnthropicClient(api_key="test-key", timeout_seconds=1, http_client=client)
    )

    response = await provider.generate(envelope())

    assert response.output is None
    assert response.telemetry.refusal is True
    assert "sensitive" not in repr(response)
    await client.aclose()


@pytest.mark.asyncio
async def test_malformed_provider_output_is_controlled() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                request=request,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "not-json"}],
                        }
                    ]
                },
            )
        )
    )
    provider = OpenAIAdapter(
        OpenAIClient(api_key="test-key", timeout_seconds=1, http_client=client)
    )

    with pytest.raises(LLMInvalidResponseError, match="malformed"):
        await provider.generate(envelope())
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_refusal_is_controlled_without_body_leakage() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                request=request,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "refusal", "refusal": "sensitive provider text"}],
                        }
                    ]
                },
            )
        )
    )
    provider = OpenAIAdapter(
        OpenAIClient(api_key="test-key", timeout_seconds=1, http_client=client)
    )

    response = await provider.generate(envelope())

    assert response.output is None
    assert response.telemetry.refusal is True
    assert "sensitive" not in repr(response)
    await client.aclose()
