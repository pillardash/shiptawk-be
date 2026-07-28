import json
from collections.abc import Mapping

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.modules.drafts.providers.protocols import (
    CredentialDecryptionError,
    PublisherPermanentError,
    PublisherTransientError,
    PublishRequest,
    PublishResult,
)


class FernetCredentialDecryptor:
    def __init__(self, keys: Mapping[str, str]) -> None:
        self._keys = dict(keys)

    def decrypt(self, ciphertext: str, key_version: str) -> Mapping[str, str]:
        key = self._keys.get(key_version)
        if key is None:
            raise CredentialDecryptionError("Credential key version is unavailable.")
        try:
            value = json.loads(
                Fernet(key.encode("ascii")).decrypt(ciphertext.encode("ascii")).decode("utf-8")
            )
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise CredentialDecryptionError(
                "Integration credentials could not be decrypted."
            ) from exc
        if not isinstance(value, dict) or not all(
            isinstance(name, str) and isinstance(secret, str) for name, secret in value.items()
        ):
            raise CredentialDecryptionError("Integration credentials are invalid.")
        return value


class HttpXPublisher:
    """X v2 publishing adapter using the workspace's decrypted OAuth credentials."""

    endpoint = "https://api.x.com/2/tweets"

    def __init__(
        self,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if transport is not None and http_client is not None:
            raise ValueError("Provide either transport or http_client, not both.")
        self._timeout = timeout_seconds
        self._http_client = http_client or httpx.AsyncClient(transport=transport)

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._http_client

    async def publish(self, request: PublishRequest) -> PublishResult:
        access_token = request.credentials.get("accessToken")
        if not access_token:
            raise PublisherPermanentError("X publishing credentials are invalid.")
        try:
            response = await self._http_client.post(
                self.endpoint,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Idempotency-Key": request.idempotency_key,
                },
                json={"text": request.content},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise PublisherTransientError("X publishing is temporarily unavailable.") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise PublisherTransientError("X publishing is temporarily unavailable.")
        if response.is_error:
            raise PublisherPermanentError("X rejected the post.")
        try:
            post_id = response.json()["data"]["id"]
        except (KeyError, TypeError, ValueError) as exc:
            raise PublisherPermanentError("X returned an invalid publish response.") from exc
        if not isinstance(post_id, str) or not post_id:
            raise PublisherPermanentError("X returned an invalid publish response.")
        return PublishResult(provider_post_id=post_id, url=f"https://x.com/i/web/status/{post_id}")
