from collections.abc import Sequence

import httpx
import pytest

from app.modules.products.providers.website_fake_provider import FakeWebsiteFetchProvider
from app.modules.products.providers.website_fetch_provider import (
    MAX_RESPONSE_BYTES,
    HTTPXWebsiteFetchProvider,
    WebsiteFetchBlockedError,
    WebsiteFetchContentTypeError,
    WebsiteFetchResult,
    WebsiteFetchTooLargeError,
)


async def public_resolver(host: str, port: int) -> Sequence[str]:
    del host, port
    return ("93.184.216.34",)


@pytest.mark.asyncio
async def test_http_provider_streams_supported_html_and_validates_redirect_dns() -> None:
    resolved: list[tuple[str, int]] = []

    async def resolver(host: str, port: int) -> Sequence[str]:
        resolved.append((host, port))
        return ("93.184.216.34",)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "https://www.example.com/page"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<h1>Page</h1>",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HTTPXWebsiteFetchProvider(http_client=client, resolver=resolver)

    result = await provider.fetch("https://example.com")

    assert result.final_url == "https://www.example.com/page"
    assert result.body == b"<h1>Page</h1>"
    assert result.redirects == ("https://www.example.com/page",)
    assert resolved == [("example.com", 443), ("www.example.com", 443)]
    await client.aclose()


@pytest.mark.asyncio
async def test_http_provider_rejects_private_dns_on_redirect_hop() -> None:
    async def resolver(host: str, port: int) -> Sequence[str]:
        del port
        return ("10.0.0.2",) if host == "private.example.com" else ("93.184.216.34",)

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302, request=request, headers={"location": "https://private.example.com/secret"}
        )
    )
    provider = HTTPXWebsiteFetchProvider(
        http_client=httpx.AsyncClient(transport=transport), resolver=resolver
    )

    with pytest.raises(WebsiteFetchBlockedError, match="non_global_address"):
        await provider.fetch("https://example.com")

    await provider.http_client.aclose()


@pytest.mark.asyncio
async def test_http_provider_rejects_unsupported_and_oversized_responses() -> None:
    async def fetch(response: httpx.Response) -> None:
        provider = HTTPXWebsiteFetchProvider(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)),
            resolver=public_resolver,
        )
        try:
            await provider.fetch("https://example.com")
        finally:
            await provider.http_client.aclose()

    with pytest.raises(WebsiteFetchContentTypeError, match="unsupported_content_type"):
        await fetch(
            httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}")
        )

    with pytest.raises(WebsiteFetchTooLargeError, match="response_too_large"):
        await fetch(
            httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"x" * (MAX_RESPONSE_BYTES + 1),
            )
        )


@pytest.mark.asyncio
async def test_fake_provider_is_deterministic() -> None:
    result = WebsiteFetchResult(
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        content_type="text/html",
        body=b"ok",
    )
    provider = FakeWebsiteFetchProvider({"https://example.com/": result})

    assert await provider.fetch("https://example.com/") == result
    assert await provider.fetch("https://example.com/") == result
    assert provider.calls == ["https://example.com/", "https://example.com/"]
