from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

import httpx


class OAuthProviderError(Exception):
    """A controlled provider failure safe to translate at the HTTP boundary."""


@dataclass(slots=True)
class OAuthIdentityData:
    subject: str
    email: str | None
    email_verified: bool
    username: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None


class OAuthProvider(Protocol):
    name: str
    display_name: str

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthIdentityData: ...


class OAuthProviderRegistry:
    def __init__(
        self, *, require_encryption: bool = False, encryption_available: bool = True
    ) -> None:
        if require_encryption and not encryption_available:
            raise ValueError("OAuth transaction encryption is required.")
        self._providers: dict[str, OAuthProvider] = {}

    def register(self, provider: OAuthProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> OAuthProvider | None:
        return self._providers.get(name.lower())

    def list(self) -> list[OAuthProvider]:
        return [self._providers[name] for name in sorted(self._providers)]


class FakeOAuthProvider:
    name = "github"
    display_name = "GitHub"

    def __init__(self, *, identity: OAuthIdentityData) -> None:
        self.identity = identity

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return "https://provider.test/authorize?" + urlencode(
            {
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "redirect_uri": redirect_uri,
            }
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthIdentityData:
        return self.identity


class GitHubOAuthProvider:
    name = "github"
    display_name = "GitHub"
    authorize_endpoint = "https://github.com/login/oauth/authorize"
    token_endpoint = "https://github.com/login/oauth/access_token"
    api_base_url = "https://api.github.com"
    scopes = "read:user user:email"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        timeout_seconds: float = 10.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
        self.http_transport = http_transport

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return (
            self.authorize_endpoint
            + "?"
            + urlencode(
                {
                    "client_id": self.client_id,
                    "redirect_uri": redirect_uri,
                    "scope": self.scopes,
                    "state": state,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthIdentityData:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.http_transport,
                headers={"Accept": "application/json", "User-Agent": "Shiptawk"},
            ) as client:
                token_response = await client.post(
                    self.token_endpoint,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "code_verifier": code_verifier,
                    },
                )
                token_response.raise_for_status()
                access_token = token_response.json().get("access_token")
                if not isinstance(access_token, str) or not access_token:
                    raise OAuthProviderError("GitHub rejected the authorization code.")
                headers = {"Authorization": f"Bearer {access_token}"}
                profile_response = await client.get(f"{self.api_base_url}/user", headers=headers)
                emails_response = await client.get(
                    f"{self.api_base_url}/user/emails", headers=headers
                )
                profile_response.raise_for_status()
                emails_response.raise_for_status()
        except OAuthProviderError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise OAuthProviderError("GitHub OAuth is temporarily unavailable.") from exc

        profile = profile_response.json()
        emails = emails_response.json()
        if (
            not isinstance(profile, dict)
            or not isinstance(emails, list)
            or not all(isinstance(item, dict) for item in emails)
        ):
            raise OAuthProviderError("GitHub returned an invalid identity response.")
        github_id = profile.get("id")
        if not isinstance(github_id, int) or github_id < 1:
            raise OAuthProviderError("GitHub returned an invalid identity response.")
        verified = [item for item in emails if item.get("verified") and item.get("email")]
        selected = next((item for item in verified if item.get("primary")), None)
        selected = selected or (verified[0] if verified else None)
        return OAuthIdentityData(
            subject=str(github_id),
            email=str(selected["email"]).strip().lower() if selected else None,
            email_verified=selected is not None,
            username=profile.get("login") if isinstance(profile.get("login"), str) else None,
            display_name=profile.get("name") if isinstance(profile.get("name"), str) else None,
            avatar_url=(
                profile.get("avatar_url") if isinstance(profile.get("avatar_url"), str) else None
            ),
        )
