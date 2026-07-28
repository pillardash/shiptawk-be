import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken


class CredentialVaultError(Exception):
    """A controlled credential-vault failure that contains no secret material."""


class CredentialVaultKeyUnavailableError(CredentialVaultError):
    pass


class CredentialVaultDataError(CredentialVaultError):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedIntegrationCredentials:
    ciphertext: str
    key_version: str


class IntegrationCredentialVault(Protocol):
    def encrypt(self, credentials: Mapping[str, str]) -> EncryptedIntegrationCredentials: ...

    def decrypt(self, ciphertext: str, key_version: str) -> Mapping[str, str]: ...


class FernetIntegrationCredentialVault:
    def __init__(self, *, keys: Mapping[str, str], active_key_version: str) -> None:
        self._keys = dict(keys)
        self._active_key_version = active_key_version

    def encrypt(self, credentials: Mapping[str, str]) -> EncryptedIntegrationCredentials:
        key = self._keys.get(self._active_key_version)
        if key is None:
            raise CredentialVaultKeyUnavailableError("Active credential key is unavailable.")
        payload = _serialize_credentials(credentials)
        try:
            ciphertext = Fernet(key.encode("ascii")).encrypt(payload).decode("ascii")
        except (UnicodeEncodeError, ValueError) as exc:
            raise CredentialVaultKeyUnavailableError(
                "Active credential key is unavailable."
            ) from exc
        return EncryptedIntegrationCredentials(ciphertext, self._active_key_version)

    def decrypt(self, ciphertext: str, key_version: str) -> Mapping[str, str]:
        key = self._keys.get(key_version)
        if key is None:
            raise CredentialVaultKeyUnavailableError("Credential key version is unavailable.")
        try:
            payload = Fernet(key.encode("ascii")).decrypt(ciphertext.encode("ascii"))
        except (InvalidToken, UnicodeEncodeError, ValueError) as exc:
            raise CredentialVaultDataError(
                "Integration credentials could not be decrypted."
            ) from exc
        return _deserialize_credentials(payload)


class DeterministicFakeIntegrationCredentialVault:
    key_version = "fake-v1"

    def encrypt(self, credentials: Mapping[str, str]) -> EncryptedIntegrationCredentials:
        payload = _serialize_credentials(credentials)
        ciphertext = "fake:" + base64.urlsafe_b64encode(payload).decode("ascii")
        return EncryptedIntegrationCredentials(ciphertext, self.key_version)

    def decrypt(self, ciphertext: str, key_version: str) -> Mapping[str, str]:
        if key_version != self.key_version:
            raise CredentialVaultKeyUnavailableError("Credential key version is unavailable.")
        if not ciphertext.startswith("fake:"):
            raise CredentialVaultDataError("Integration credentials could not be decrypted.")
        try:
            payload = base64.urlsafe_b64decode(ciphertext.removeprefix("fake:").encode("ascii"))
        except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
            raise CredentialVaultDataError(
                "Integration credentials could not be decrypted."
            ) from exc
        return _deserialize_credentials(payload)


def _serialize_credentials(credentials: Mapping[str, str]) -> bytes:
    values = dict(credentials)
    if not all(
        isinstance(name, str) and isinstance(secret, str) for name, secret in values.items()
    ):
        raise CredentialVaultDataError("Integration credentials are invalid.")
    return json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _deserialize_credentials(payload: bytes) -> Mapping[str, str]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialVaultDataError("Integration credentials are invalid.") from exc
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and isinstance(secret, str) for name, secret in value.items()
    ):
        raise CredentialVaultDataError("Integration credentials are invalid.")
    return value


__all__ = [
    "CredentialVaultDataError",
    "CredentialVaultError",
    "CredentialVaultKeyUnavailableError",
    "DeterministicFakeIntegrationCredentialVault",
    "EncryptedIntegrationCredentials",
    "FernetIntegrationCredentialVault",
    "IntegrationCredentialVault",
]
