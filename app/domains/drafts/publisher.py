import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx
from cryptography.fernet import Fernet, InvalidToken


class PublisherTransientError(Exception):
    """A provider failure that can be retried with the same idempotency key."""


class PublisherPermanentError(Exception):
    """A provider rejection that retrying unchanged will not resolve."""


class CredentialDecryptionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PublishRequest:
    content: str
    credentials: Mapping[str, str]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class PublishResult:
    provider_post_id: str
    url: str


class Publisher(Protocol):
    async def publish(self, request: PublishRequest) -> PublishResult: ...


class CredentialDecryptor(Protocol):
    def decrypt(self, ciphertext: str, key_version: str) -> Mapping[str, str]: ...


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


class StaticCredentialDecryptor:
    """Deterministic credential decoder for tests and local adapters."""

    def __init__(self, credentials: Mapping[str, Mapping[str, str]]) -> None:
        self._credentials = dict(credentials)

    def decrypt(self, ciphertext: str, key_version: str) -> Mapping[str, str]:
        del key_version
        try:
            return self._credentials[ciphertext]
        except KeyError as exc:
            raise CredentialDecryptionError(
                "Integration credentials could not be decrypted."
            ) from exc


class FakePublisher:
    """Idempotent provider fake; no network or live credentials are used."""

    def __init__(self) -> None:
        self.requests: list[PublishRequest] = []
        self.error: Exception | None = None
        self._results: dict[str, PublishResult] = {}

    async def publish(self, request: PublishRequest) -> PublishResult:
        if self.error is not None:
            raise self.error
        existing = self._results.get(request.idempotency_key)
        if existing is not None:
            return existing
        self.requests.append(request)
        result = PublishResult(
            provider_post_id=f"fake-post-{len(self.requests)}",
            url=f"https://x.example/fake-post-{len(self.requests)}",
        )
        self._results[request.idempotency_key] = result
        return result


class HttpXPublisher:
    """X v2 publishing adapter using the workspace's decrypted OAuth credentials."""

    endpoint = "https://api.x.com/2/tweets"

    def __init__(
        self,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._transport = transport

    async def publish(self, request: PublishRequest) -> PublishResult:
        access_token = request.credentials.get("accessToken")
        if not access_token:
            raise PublisherPermanentError("X publishing credentials are invalid.")
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    self.endpoint,
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
