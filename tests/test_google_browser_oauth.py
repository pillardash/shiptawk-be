from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.modules.identity.providers.google_browser_oauth_provider import (
    GoogleBrowserOAuthProvider,
)
from app.services.oauth import OAuthProviderError


def google_transport(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/token":
        form = parse_qs(request.content.decode())
        assert form["code_verifier"] == ["verifier"]
        assert form["grant_type"] == ["authorization_code"]
        return httpx.Response(200, json={"access_token": "ephemeral-access-token"})
    if request.url.path == "/v1/userinfo":
        assert request.headers["Authorization"] == "Bearer ephemeral-access-token"
        return httpx.Response(
            200,
            json={
                "sub": "google-subject-1",
                "email": " Person@Example.COM ",
                "email_verified": True,
                "name": "Person Name",
                "picture": "https://images.example/person",
            },
        )
    raise AssertionError(f"Unexpected request: {request.url}")


@pytest.mark.asyncio
async def test_google_browser_login_uses_exact_scopes_pkce_and_strict_userinfo() -> None:
    provider = GoogleBrowserOAuthProvider(
        client_id="login-client",
        client_secret="login-secret",
        http_transport=httpx.MockTransport(google_transport),
    )
    query = parse_qs(
        urlsplit(
            provider.authorization_url(
                state="state", code_challenge="challenge", redirect_uri="https://api/callback"
            )
        ).query
    )
    assert query == {
        "client_id": ["login-client"],
        "redirect_uri": ["https://api/callback"],
        "response_type": ["code"],
        "scope": ["openid email profile"],
        "state": ["state"],
        "code_challenge": ["challenge"],
        "code_challenge_method": ["S256"],
    }
    assert "access_type" not in query
    assert "prompt" not in query
    assert all("webmasters" not in value for values in query.values() for value in values)

    identity = await provider.exchange_code(
        code="code", code_verifier="verifier", redirect_uri="https://api/callback"
    )
    assert identity.subject == "google-subject-1"
    assert identity.email == "person@example.com"
    assert identity.email_verified is True
    assert identity.username is None
    assert identity.display_name == "Person Name"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        {},
        {"sub": "", "email": "person@example.com", "email_verified": True},
        {"sub": "subject", "email": "not-an-email", "email_verified": True},
        {"sub": "subject", "email": "person@example.com", "email_verified": False},
    ],
)
async def test_google_browser_login_rejects_incomplete_or_unverified_identity(
    profile: dict[str, object],
) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(200, json=profile)

    provider = GoogleBrowserOAuthProvider(
        client_id="client",
        client_secret="secret",
        http_transport=httpx.MockTransport(transport),
    )
    with pytest.raises(OAuthProviderError, match="invalid identity response"):
        await provider.exchange_code(
            code="code", code_verifier="verifier", redirect_uri="https://api/callback"
        )


@pytest.mark.asyncio
async def test_google_browser_login_converts_timeout_without_exposing_secrets() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("transport detail")

    provider = GoogleBrowserOAuthProvider(
        client_id="client",
        client_secret="do-not-expose",
        http_transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(OAuthProviderError, match="temporarily unavailable") as error:
        await provider.exchange_code(
            code="secret-code", code_verifier="secret-verifier", redirect_uri="https://api/callback"
        )
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_payload", "profile_payload", "message"),
    [
        ({}, {}, "rejected the authorization code"),
        ({"access_token": "token"}, [], "invalid identity response"),
    ],
)
async def test_google_browser_login_rejects_malformed_provider_payloads(
    token_payload: object, profile_payload: object, message: str
) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=token_payload if request.url.path == "/token" else profile_payload,
        )

    provider = GoogleBrowserOAuthProvider(
        client_id="client",
        client_secret="secret",
        http_transport=httpx.MockTransport(transport),
    )
    with pytest.raises(OAuthProviderError, match=message):
        await provider.exchange_code(
            code="code", code_verifier="verifier", redirect_uri="https://api/callback"
        )


@pytest.mark.asyncio
async def test_google_browser_provider_rejects_two_http_client_sources() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="either"):
            GoogleBrowserOAuthProvider(
                client_id="client",
                client_secret="secret",
                http_transport=httpx.MockTransport(google_transport),
                http_client=client,
            )
