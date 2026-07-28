from dataclasses import dataclass

import httpx

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class SharedHTTPClients:
    openai: httpx.AsyncClient
    github: httpx.AsyncClient
    x: httpx.AsyncClient
    website: httpx.AsyncClient
    search: httpx.AsyncClient

    def all(self) -> tuple[httpx.AsyncClient, ...]:
        return (self.openai, self.github, self.x, self.website, self.search)

    async def close(self) -> None:
        for client in self.all():
            await client.aclose()


def create_http_clients(settings: Settings) -> SharedHTTPClients:
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    return SharedHTTPClients(
        openai=httpx.AsyncClient(
            timeout=settings.generation_timeout_seconds,
            limits=limits,
        ),
        github=httpx.AsyncClient(
            timeout=settings.oauth_http_timeout_seconds,
            limits=limits,
        ),
        x=httpx.AsyncClient(
            timeout=settings.oauth_http_timeout_seconds,
            limits=limits,
        ),
        website=httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        ),
        search=httpx.AsyncClient(
            timeout=settings.oauth_http_timeout_seconds,
            limits=limits,
        ),
    )
