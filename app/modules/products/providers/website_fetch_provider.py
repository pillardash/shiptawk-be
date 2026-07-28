import asyncio
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from ipaddress import ip_address
from types import TracebackType
from typing import Protocol, Self
from urllib.parse import urljoin, urlsplit

import httpx

from app.modules.products.policies.website_url_policy import normalize_website_url

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 10
_SUPPORTED_CONTENT_TYPES = frozenset(
    {"application/xhtml+xml", "application/xml", "text/html", "text/plain", "text/xml"}
)

HostResolver = Callable[[str, int], Awaitable[Sequence[str]]]


@dataclass(frozen=True, slots=True)
class WebsiteFetchResult:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    redirects: tuple[str, ...] = ()


class WebsiteFetchError(Exception):
    retryable = False

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class WebsiteFetchTransientError(WebsiteFetchError):
    retryable = True


class WebsiteFetchBlockedError(WebsiteFetchError):
    pass


class WebsiteFetchContentTypeError(WebsiteFetchError):
    pass


class WebsiteFetchTooLargeError(WebsiteFetchError):
    pass


class WebsiteFetchProvider(Protocol):
    async def fetch(self, url: str) -> WebsiteFetchResult: ...


async def _resolve_host(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    try:
        records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise WebsiteFetchTransientError("dns_failure") from exc
    return tuple({record[4][0] for record in records})


class HTTPXWebsiteFetchProvider:
    """Bounded public HTML/XML fetcher with explicit redirect validation."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10,
        http_client: httpx.AsyncClient | None = None,
        resolver: HostResolver = _resolve_host,
    ) -> None:
        self._timeout = timeout_seconds
        self._resolver = resolver
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._http_client

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http_client.aclose()

    async def _validate_dns(self, url: str) -> None:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host is None:
            raise WebsiteFetchBlockedError("invalid_url")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = await self._resolver(host, port)
        except WebsiteFetchError:
            raise
        except OSError as exc:
            raise WebsiteFetchTransientError("dns_failure") from exc
        if not addresses:
            raise WebsiteFetchTransientError("dns_failure")
        try:
            if any(not ip_address(address).is_global for address in addresses):
                raise WebsiteFetchBlockedError("non_global_address")
        except ValueError as exc:
            raise WebsiteFetchBlockedError("invalid_dns_response") from exc

    async def fetch(self, url: str) -> WebsiteFetchResult:
        try:
            requested_url = normalize_website_url(url)
        except ValueError as exc:
            raise WebsiteFetchBlockedError("invalid_url") from exc
        current_url = requested_url
        redirects: list[str] = []

        for redirect_count in range(MAX_REDIRECTS + 1):
            await self._validate_dns(current_url)
            try:
                async with self._http_client.stream(
                    "GET",
                    current_url,
                    headers={
                        "Accept": (
                            "text/html,application/xhtml+xml,application/xml,text/xml,text/plain"
                        ),
                        "User-Agent": "ShiptawkWebsiteCrawler/1.0",
                    },
                    follow_redirects=False,
                    timeout=self._timeout,
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise WebsiteFetchBlockedError("redirect_without_location")
                        if redirect_count == MAX_REDIRECTS:
                            raise WebsiteFetchBlockedError("too_many_redirects")
                        try:
                            current_url = normalize_website_url(urljoin(current_url, location))
                        except ValueError as exc:
                            raise WebsiteFetchBlockedError("invalid_redirect_url") from exc
                        redirects.append(current_url)
                        continue

                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type not in _SUPPORTED_CONTENT_TYPES:
                        raise WebsiteFetchContentTypeError("unsupported_content_type")
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            if int(content_length) > MAX_RESPONSE_BYTES:
                                raise WebsiteFetchTooLargeError("response_too_large")
                        except ValueError as exc:
                            raise WebsiteFetchError("invalid_content_length") from exc
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise WebsiteFetchTooLargeError("response_too_large")
                    return WebsiteFetchResult(
                        requested_url=requested_url,
                        final_url=normalize_website_url(str(response.url)),
                        status_code=response.status_code,
                        content_type=content_type,
                        body=bytes(body),
                        redirects=tuple(redirects),
                    )
            except WebsiteFetchError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise WebsiteFetchTransientError("transport_failure") from exc

        raise WebsiteFetchBlockedError("too_many_redirects")


__all__ = [
    "MAX_RESPONSE_BYTES",
    "HTTPXWebsiteFetchProvider",
    "WebsiteFetchBlockedError",
    "WebsiteFetchContentTypeError",
    "WebsiteFetchError",
    "WebsiteFetchProvider",
    "WebsiteFetchResult",
    "WebsiteFetchTooLargeError",
    "WebsiteFetchTransientError",
]
