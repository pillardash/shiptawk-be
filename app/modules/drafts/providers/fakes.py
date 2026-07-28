from collections.abc import Mapping

from app.modules.drafts.providers.protocols import (
    CredentialDecryptionError,
    PublishRequest,
    PublishResult,
)


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


__all__ = ["FakePublisher", "StaticCredentialDecryptor"]
