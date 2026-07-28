from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode

import httpx

from app.modules.integrations.providers.credential_vault_provider import (
    EncryptedIntegrationCredentials,
    FernetIntegrationCredentialVault,
    IntegrationCredentialVault,
)


class XOAuthProviderError(Exception):
    """A sanitized provider failure that never contains credentials or response bodies."""


@dataclass(frozen=True, slots=True)
class XOAuthGrant:
    account_id: str
    username: str | None
    access_token: str
    refresh_token: str | None
    scopes: list[str]
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class XOAuthExchange:
    code: str
    code_verifier: str
    redirect_uri: str


class XOAuthProvider(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> XOAuthGrant: ...


IntegrationCredentialCipher = IntegrationCredentialVault


class FernetIntegrationCredentialCipher(FernetIntegrationCredentialVault):
    def __init__(self, *, key: str, key_version: str = "v1") -> None:
        super().__init__(keys={key_version: key}, active_key_version=key_version)


class FakeIntegrationCredentialCipher:
    def __init__(self) -> None:
        self.values: list[dict[str, str]] = []

    def encrypt(self, credentials: Mapping[str, str]) -> EncryptedIntegrationCredentials:
        self.values.append(dict(credentials))
        return EncryptedIntegrationCredentials(
            ciphertext=f"encrypted:{len(self.values)}", key_version="fake-v1"
        )

    def decrypt(self, ciphertext: str, key_version: str) -> Mapping[str, str]:
        if key_version != "fake-v1" or not ciphertext.startswith("encrypted:"):
            raise ValueError("Invalid fake integration credentials.")
        index = int(ciphertext.removeprefix("encrypted:")) - 1
        return self.values[index]


class FakeXOAuthProvider:
    def __init__(self, *, grant: XOAuthGrant) -> None:
        self.grant = grant
        self.exchanges: list[XOAuthExchange] = []
        self.error: Exception | None = None

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return "https://x.example/authorize?" + urlencode(
            {
                "response_type": "code",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "redirect_uri": redirect_uri,
            }
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> XOAuthGrant:
        self.exchanges.append(XOAuthExchange(code, code_verifier, redirect_uri))
        if self.error is not None:
            raise self.error
        return self.grant


class HttpXOAuthProvider:
    authorize_endpoint = "https://x.com/i/oauth2/authorize"
    token_endpoint = "https://api.x.com/2/oauth2/token"
    me_endpoint = "https://api.x.com/2/users/me"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        scopes: list[str],
        timeout_seconds: float = 10.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if http_transport is not None and http_client is not None:
            raise ValueError("Provide either http_transport or http_client, not both.")
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
        self._http_client = http_client or httpx.AsyncClient(transport=http_transport)

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._http_client

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return (
            self.authorize_endpoint
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": self.client_id,
                    "redirect_uri": redirect_uri,
                    "scope": " ".join(self.scopes),
                    "state": state,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> XOAuthGrant:
        try:
            common_headers = {"Accept": "application/json", "User-Agent": "Shiptawk"}
            token_response = await self._http_client.post(
                self.token_endpoint,
                timeout=self.timeout,
                headers=common_headers,
                auth=(self.client_id, self.client_secret),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                },
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            access_token = token_payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise XOAuthProviderError("X rejected the authorization code.")
            profile_response = await self._http_client.get(
                self.me_endpoint,
                timeout=self.timeout,
                headers={**common_headers, "Authorization": f"Bearer {access_token}"},
                params={"user.fields": "id,username"},
            )
            profile_response.raise_for_status()
        except XOAuthProviderError:
            raise
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise XOAuthProviderError("X OAuth is temporarily unavailable.") from exc

        profile_payload = profile_response.json()
        profile = profile_payload.get("data") if isinstance(profile_payload, dict) else None
        account_id = profile.get("id") if isinstance(profile, dict) else None
        if not isinstance(account_id, str) or not account_id:
            raise XOAuthProviderError("X returned an invalid account response.")
        assert isinstance(profile, dict)
        scope = token_payload.get("scope")
        scopes = scope.split() if isinstance(scope, str) else []
        if "users.read" not in scopes:
            raise XOAuthProviderError("X did not grant the required account scope.")
        expires_in = token_payload.get("expires_in")
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=expires_in)
            if isinstance(expires_in, int) and expires_in > 0
            else None
        )
        refresh_token = token_payload.get("refresh_token")
        return XOAuthGrant(
            account_id=account_id,
            username=profile.get("username") if isinstance(profile.get("username"), str) else None,
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            scopes=scopes,
            expires_at=expires_at,
        )


__all__ = [
    "EncryptedIntegrationCredentials",
    "FakeIntegrationCredentialCipher",
    "FakeXOAuthProvider",
    "FernetIntegrationCredentialCipher",
    "HttpXOAuthProvider",
    "IntegrationCredentialCipher",
    "XOAuthExchange",
    "XOAuthGrant",
    "XOAuthProvider",
    "XOAuthProviderError",
]
