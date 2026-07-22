import httpx
import pytest

from app.services.oauth import GitHubOAuthProvider, OAuthProviderError


def github_transport(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/login/oauth/access_token":
        assert request.method == "POST"
        assert "code_verifier=verifier" in request.content.decode()
        return httpx.Response(
            200, json={"access_token": "short-lived", "scope": "read:user,user:email"}
        )
    if request.url.path == "/user":
        assert request.headers["Authorization"] == "Bearer short-lived"
        return httpx.Response(
            200,
            json={"id": 12345, "login": "octocat", "name": "Octo Cat", "avatar_url": None},
        )
    if request.url.path == "/user/emails":
        return httpx.Response(
            200,
            json=[
                {"email": "other@example.com", "primary": False, "verified": True},
                {"email": "primary@example.com", "primary": True, "verified": True},
            ],
        )
    raise AssertionError(f"Unexpected request: {request.url}")


@pytest.mark.asyncio
async def test_github_adapter_uses_pkce_and_primary_verified_email() -> None:
    provider = GitHubOAuthProvider(
        client_id="client",
        client_secret="secret",
        http_transport=httpx.MockTransport(github_transport),
    )

    identity = await provider.exchange_code(
        code="code", code_verifier="verifier", redirect_uri="https://api.example/callback"
    )

    assert identity.subject == "12345"
    assert identity.email == "primary@example.com"
    assert identity.email_verified is True
    assert provider.authorization_url(
        state="state", code_challenge="challenge", redirect_uri="https://api.example/callback"
    ).startswith("https://github.com/login/oauth/authorize?")
    assert "scope=read%3Auser+user%3Aemail" in provider.authorization_url(
        state="state", code_challenge="challenge", redirect_uri="https://api.example/callback"
    )


@pytest.mark.asyncio
async def test_github_adapter_converts_transport_errors_to_controlled_error() -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    provider = GitHubOAuthProvider(
        client_id="client",
        client_secret="secret",
        http_transport=httpx.MockTransport(fail),
    )

    with pytest.raises(OAuthProviderError, match="temporarily unavailable"):
        await provider.exchange_code(
            code="code", code_verifier="verifier", redirect_uri="https://api.example/callback"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/user", []),
        ("/user/emails", ["not-an-email-record"]),
    ],
)
async def test_github_adapter_converts_malformed_json_to_controlled_error(
    path: str, payload: object
) -> None:
    def malformed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "short-lived"})
        if request.url.path == path:
            return httpx.Response(200, json=payload)
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 12345})
        if request.url.path == "/user/emails":
            return httpx.Response(200, json=[])
        raise AssertionError(f"Unexpected request: {request.url}")

    provider = GitHubOAuthProvider(
        client_id="client",
        client_secret="secret",
        http_transport=httpx.MockTransport(malformed),
    )

    with pytest.raises(OAuthProviderError, match="invalid identity response"):
        await provider.exchange_code(
            code="code", code_verifier="verifier", redirect_uri="https://api.example/callback"
        )
