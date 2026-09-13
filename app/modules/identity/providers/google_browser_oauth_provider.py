from urllib.parse import urlencode

import httpx
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.services.oauth import OAuthIdentityData, OAuthProviderError


class GoogleBrowserOAuthProvider:
    name = "google"
    display_name = "Google"
    authorize_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    userinfo_endpoint = "https://openidconnect.googleapis.com/v1/userinfo"
    scopes = "openid email profile"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        timeout_seconds: float = 10.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if http_transport is not None and http_client is not None:
            raise ValueError("Provide either http_transport or http_client, not both.")
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
        self._http_client = http_client or httpx.AsyncClient(transport=http_transport)

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return (
            self.authorize_endpoint
            + "?"
            + urlencode(
                {
                    "client_id": self.client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
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
            token_response = await self._http_client.post(
                self.token_endpoint,
                timeout=self.timeout,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "code_verifier": code_verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            access_token = (
                token_payload.get("access_token") if isinstance(token_payload, dict) else None
            )
            if not isinstance(access_token, str) or not access_token:
                raise OAuthProviderError("Google rejected the authorization code.")
            userinfo_response = await self._http_client.get(
                self.userinfo_endpoint,
                timeout=self.timeout,
                headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
            )
            userinfo_response.raise_for_status()
            profile = userinfo_response.json()
        except OAuthProviderError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise OAuthProviderError("Google login is temporarily unavailable.") from exc

        if not isinstance(profile, dict):
            raise OAuthProviderError("Google returned an invalid identity response.")
        subject = profile.get("sub")
        email = profile.get("email")
        if (
            not isinstance(subject, str)
            or not subject.strip()
            or not isinstance(email, str)
            or profile.get("email_verified") is not True
        ):
            raise OAuthProviderError("Google returned an invalid identity response.")
        try:
            normalized_email = str(TypeAdapter(EmailStr).validate_python(email.strip())).lower()
        except ValidationError as exc:
            raise OAuthProviderError("Google returned an invalid identity response.") from exc
        return OAuthIdentityData(
            subject=subject.strip(),
            email=normalized_email,
            email_verified=True,
            display_name=profile.get("name") if isinstance(profile.get("name"), str) else None,
            avatar_url=profile.get("picture") if isinstance(profile.get("picture"), str) else None,
        )
