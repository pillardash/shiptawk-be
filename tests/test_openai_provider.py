import httpx
import pytest

from app.domains.generation.models import GenerationRequest
from app.domains.generation.openai_provider import OpenAIHTTPGenerationProvider
from app.domains.generation.provider import ProviderPermanentError


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
async def test_permanent_openai_http_errors_are_controlled(status_code: int) -> None:
    provider = OpenAIHTTPGenerationProvider(
        api_key="not-logged",
        timeout_seconds=1,
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code, request=request)),
    )
    request = GenerationRequest(
        model="test-model",
        prompt_version="test.v1",
        system_instructions="Return JSON",
        input_text="Sanitized input",
        parameters={},
        correlation_id="test-correlation",
    )

    with pytest.raises(ProviderPermanentError, match="openai_permanent_failure"):
        await provider.generate(request)
