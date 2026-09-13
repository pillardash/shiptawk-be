from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Any, cast
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest

from app.modules.products.providers.website_fake_provider import FakeWebsiteFetchProvider
from app.modules.products.providers.website_fetch_provider import (
    MAX_RESPONSE_BYTES,
    HTTPXWebsiteFetchProvider,
    WebsiteFetchBlockedError,
    WebsiteFetchContentTypeError,
    WebsiteFetchError,
    WebsiteFetchResult,
    WebsiteFetchTooLargeError,
    WebsiteFetchTransientError,
    _HTTPcoreTransport,
    _validated_addresses,
    _ValidatedNetworkBackend,
)


async def public_resolver(host: str, port: int) -> Sequence[str]:
    del host, port
    return ("93.184.216.34",)


class RecordingNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connections: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del timeout, local_address, socket_options
        self.connections.append((host, port))
        raise httpcore.ConnectError("stop after recording")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise AssertionError("unix sockets are not used")

    async def sleep(self, seconds: float) -> None:
        del seconds


class SuccessfulNetworkBackend(RecordingNetworkBackend):
    def __init__(self) -> None:
        super().__init__()
        self.stream = cast(httpcore.AsyncNetworkStream, object())
        self.unix_stream = cast(httpcore.AsyncNetworkStream, object())
        self.sleeps: list[float] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del timeout, local_address, socket_options
        self.connections.append((host, port))
        if len(self.connections) == 1:
            raise OSError("first address unavailable")
        return self.stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        return self.unix_stream

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [
        ("93.184.216.34", "10.0.0.1"),
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("192.0.2.1",),
        ("::1",),
        ("fe80::1",),
        ("2001:db8::1",),
    ],
)
async def test_network_backend_rejects_any_non_global_answer_before_connect(
    addresses: tuple[str, ...],
) -> None:
    async def resolver(host: str, port: int) -> Sequence[str]:
        del host, port
        return addresses

    delegate = RecordingNetworkBackend()
    backend = _ValidatedNetworkBackend(resolver, delegate)

    with pytest.raises(WebsiteFetchBlockedError, match="non_global_address"):
        await backend.connect_tcp("example.com", 443)

    assert delegate.connections == []


@pytest.mark.asyncio
async def test_network_backend_connects_to_validated_ip_not_hostname() -> None:
    delegate = RecordingNetworkBackend()
    backend = _ValidatedNetworkBackend(public_resolver, delegate)

    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("example.com", 443)

    assert delegate.connections == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_network_backend_falls_back_and_delegates_other_operations() -> None:
    async def resolver(host: str, port: int) -> Sequence[str]:
        del host, port
        return ("93.184.216.34", "1.1.1.1")

    delegate = SuccessfulNetworkBackend()
    backend = _ValidatedNetworkBackend(resolver, delegate)

    assert await backend.connect_tcp("example.com", 443) is delegate.stream
    assert delegate.connections == [("93.184.216.34", 443), ("1.1.1.1", 443)]
    assert await backend.connect_unix_socket("/tmp/test.sock") is delegate.unix_stream
    await backend.sleep(0.25)
    assert delegate.sleeps == [0.25]


@pytest.mark.asyncio
async def test_network_backend_reraises_last_connection_failure() -> None:
    async def resolver(host: str, port: int) -> Sequence[str]:
        del host, port
        return ("93.184.216.34", "1.1.1.1")

    delegate = RecordingNetworkBackend()
    backend = _ValidatedNetworkBackend(resolver, delegate)

    with pytest.raises(httpcore.ConnectError, match="stop after recording"):
        await backend.connect_tcp("example.com", 443)

    assert delegate.connections == [("93.184.216.34", 443), ("1.1.1.1", 443)]


@pytest.mark.asyncio
async def test_address_validation_classifies_resolver_failures() -> None:
    async def empty(host: str, port: int) -> Sequence[str]:
        del host, port
        return ()

    async def invalid(host: str, port: int) -> Sequence[str]:
        del host, port
        return ("not-an-address",)

    async def os_failure(host: str, port: int) -> Sequence[str]:
        del host, port
        raise OSError("dns down")

    async def classified(host: str, port: int) -> Sequence[str]:
        del host, port
        raise WebsiteFetchBlockedError("blocked")

    with pytest.raises(WebsiteFetchTransientError, match="dns_failure"):
        await _validated_addresses(empty, "example.com", 443)
    with pytest.raises(WebsiteFetchBlockedError, match="invalid_dns_response"):
        await _validated_addresses(invalid, "example.com", 443)
    with pytest.raises(WebsiteFetchTransientError, match="dns_failure"):
        await _validated_addresses(os_failure, "example.com", 443)
    with pytest.raises(WebsiteFetchError, match="blocked"):
        await _validated_addresses(classified, "example.com", 443)


@pytest.mark.asyncio
async def test_custom_transport_adapts_response_stream_and_closes_pool() -> None:
    class Stream:
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"one"
            yield b"two"

        async def aclose(self) -> None:
            self.closed = True

    stream = Stream()
    pool = AsyncMock()
    pool.handle_async_request.return_value = httpcore.Response(
        200, headers=[(b"content-type", b"text/plain")], content=stream
    )
    transport = cast(Any, object.__new__(_HTTPcoreTransport))
    transport._pool = pool

    response = await transport.handle_async_request(httpx.Request("GET", "https://example.com/a"))
    assert response.status_code == 200
    assert b"".join([part async for part in response.aiter_bytes()]) == b"onetwo"
    await response.aclose()
    assert stream.closed is True
    await transport.aclose()
    pool.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_closes_only_the_client_it_owns() -> None:
    owned = HTTPXWebsiteFetchProvider()
    async with owned as entered:
        assert entered is owned
    assert owned.http_client.is_closed

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    external = HTTPXWebsiteFetchProvider(http_client=client)
    await external.aclose()
    assert not client.is_closed
    await client.aclose()


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
@pytest.mark.parametrize(
    ("status", "location", "code"),
    [
        (302, None, "redirect_without_location"),
        (302, "ftp://example.com/file", "invalid_redirect_url"),
    ],
)
async def test_http_provider_rejects_invalid_redirects(
    status: int, location: str | None, code: str
) -> None:
    headers = {"location": location} if location is not None else {}
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, headers=headers))
    )
    provider = HTTPXWebsiteFetchProvider(http_client=client, resolver=public_resolver)
    try:
        with pytest.raises(WebsiteFetchBlockedError, match=code):
            await provider.fetch("https://example.com")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_http_provider_enforces_redirect_limit() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"location": "/next"})
        )
    )
    provider = HTTPXWebsiteFetchProvider(http_client=client, resolver=public_resolver)
    try:
        with pytest.raises(WebsiteFetchBlockedError, match="too_many_redirects"):
            await provider.fetch("https://example.com")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_http_provider_classifies_transport_failure() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    provider = HTTPXWebsiteFetchProvider(http_client=client, resolver=public_resolver)
    try:
        with pytest.raises(WebsiteFetchTransientError, match="transport_failure"):
            await provider.fetch("https://example.com")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_http_provider_rejects_invalid_content_length() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html", "content-length": "unknown"},
            )
        )
    )
    provider = HTTPXWebsiteFetchProvider(http_client=client, resolver=public_resolver)
    try:
        with pytest.raises(WebsiteFetchError, match="invalid_content_length"):
            await provider.fetch("https://example.com")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_http_provider_rejects_body_that_grows_past_limit_while_streaming() -> None:
    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"x" * MAX_RESPONSE_BYTES
            yield b"x"

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/html"}, stream=OversizedStream()
            )
        )
    )
    provider = HTTPXWebsiteFetchProvider(http_client=client, resolver=public_resolver)
    try:
        with pytest.raises(WebsiteFetchTooLargeError, match="response_too_large"):
            await provider.fetch("https://example.com")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_http_provider_rejects_invalid_requested_url_before_network_access() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("network must not be called"))
    )
    provider = HTTPXWebsiteFetchProvider(http_client=client, resolver=public_resolver)
    try:
        with pytest.raises(WebsiteFetchBlockedError, match="invalid_url"):
            await provider.fetch("ftp://example.com")
    finally:
        await client.aclose()


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
