from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


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


__all__ = [
    "CredentialDecryptionError",
    "CredentialDecryptor",
    "PublishRequest",
    "PublishResult",
    "Publisher",
    "PublisherPermanentError",
    "PublisherTransientError",
]
