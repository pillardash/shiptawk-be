import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import generate_pkce_verifier, generate_token, hash_token
from app.domains.integrations.models import IntegrationConnection, IntegrationOAuthTransaction
from app.shared.exceptions import BadRequestError


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


@dataclass(frozen=True, slots=True)
class EncryptedIntegrationCredentials:
    ciphertext: str
    key_version: str


class IntegrationCredentialCipher(Protocol):
    def encrypt(self, credentials: Mapping[str, str]) -> EncryptedIntegrationCredentials: ...


class FernetIntegrationCredentialCipher:
    def __init__(self, *, key: str, key_version: str = "v1") -> None:
        self._fernet = Fernet(key.encode("ascii"))
        self._key_version = key_version

    def encrypt(self, credentials: Mapping[str, str]) -> EncryptedIntegrationCredentials:
        payload = json.dumps(dict(credentials), sort_keys=True, separators=(",", ":")).encode()
        return EncryptedIntegrationCredentials(
            ciphertext=self._fernet.encrypt(payload).decode("ascii"),
            key_version=self._key_version,
        )


class FakeIntegrationCredentialCipher:
    def __init__(self) -> None:
        self.values: list[dict[str, str]] = []

    def encrypt(self, credentials: Mapping[str, str]) -> EncryptedIntegrationCredentials:
        self.values.append(dict(credentials))
        return EncryptedIntegrationCredentials(
            ciphertext=f"encrypted:{len(self.values)}", key_version="fake-v1"
        )


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
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
        self.http_transport = http_transport

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
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.http_transport,
                headers={"Accept": "application/json", "User-Agent": "Shiptawk"},
            ) as client:
                token_response = await client.post(
                    self.token_endpoint,
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
                profile_response = await client.get(
                    self.me_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
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


def validate_return_path(return_path: str) -> str:
    parsed = urlsplit(return_path)
    if (
        not return_path.startswith("/")
        or return_path.startswith("//")
        or "\\" in return_path
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
    ):
        raise BadRequestError("Invalid return path.", code="invalid_return_path")
    return return_path


async def create_x_oauth_transaction(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    return_path: str,
    redirect_uri: str,
) -> tuple[str, str]:
    return_path = validate_return_path(return_path)
    key = get_settings().oauth_transaction_encryption_key
    if key is None:
        raise RuntimeError("OAuth transaction encryption is not configured.")
    state = generate_token()
    verifier = generate_pkce_verifier()
    db.add(
        IntegrationOAuthTransaction(
            workspace_id=workspace_id,
            user_id=user_id,
            provider="x",
            state_hash=hash_token(state),
            code_verifier_ciphertext=Fernet(key.get_secret_value().encode("ascii"))
            .encrypt(verifier.encode())
            .decode("ascii"),
            return_path=return_path,
            redirect_uri=redirect_uri,
            expires_at=datetime.now(UTC)
            + timedelta(minutes=get_settings().oauth_transaction_expire_minutes),
            created_by=user_id,
        )
    )
    await db.commit()
    return state, verifier


async def consume_x_oauth_transaction(
    db: AsyncSession, *, workspace_id: UUID, user_id: UUID, state: str
) -> tuple[str, str, str]:
    now = datetime.now(UTC)
    row = (
        await db.execute(
            update(IntegrationOAuthTransaction)
            .where(
                IntegrationOAuthTransaction.workspace_id == workspace_id,
                IntegrationOAuthTransaction.user_id == user_id,
                IntegrationOAuthTransaction.provider == "x",
                IntegrationOAuthTransaction.state_hash == hash_token(state),
                IntegrationOAuthTransaction.consumed_at.is_(None),
                IntegrationOAuthTransaction.expires_at > now,
            )
            .values(consumed_at=now, updated_by=user_id)
            .returning(
                IntegrationOAuthTransaction.code_verifier_ciphertext,
                IntegrationOAuthTransaction.return_path,
                IntegrationOAuthTransaction.redirect_uri,
            )
        )
    ).one_or_none()
    if row is None:
        await db.rollback()
        raise BadRequestError("Invalid or expired OAuth state.", code="invalid_oauth_state")
    await db.commit()
    key = get_settings().oauth_transaction_encryption_key
    if key is None:
        raise RuntimeError("OAuth transaction encryption is not configured.")
    try:
        verifier = (
            Fernet(key.get_secret_value().encode("ascii"))
            .decrypt(row.code_verifier_ciphertext.encode("ascii"))
            .decode()
        )
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise BadRequestError("Invalid OAuth transaction.", code="invalid_oauth_state") from exc
    return verifier, row.return_path, row.redirect_uri


async def save_x_connection(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    grant: XOAuthGrant,
    cipher: IntegrationCredentialCipher,
) -> IntegrationConnection:
    if "users.read" not in grant.scopes:
        raise XOAuthProviderError("X did not grant the required account scope.")
    credentials = {"accessToken": grant.access_token}
    if grant.refresh_token:
        credentials["refreshToken"] = grant.refresh_token
    encrypted = cipher.encrypt(credentials)
    existing = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider.in_(("x", "twitter")),
            IntegrationConnection.external_account_id == grant.account_id,
        )
    )
    now = datetime.now(UTC)
    await db.execute(
        update(IntegrationConnection)
        .where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider.in_(("x", "twitter")),
            IntegrationConnection.external_account_id != grant.account_id,
            IntegrationConnection.deleted_at.is_(None),
        )
        .values(status="revoked", deleted_at=now, updated_by=user_id)
    )
    if existing is None:
        existing = IntegrationConnection(
            workspace_id=workspace_id,
            provider="x",
            external_account_id=grant.account_id,
            idempotency_key=f"x:{workspace_id}:{grant.account_id}",
            created_by=user_id,
        )
        db.add(existing)
    existing.provider = "x"
    existing.external_username = grant.username
    existing.credentials_ciphertext = encrypted.ciphertext
    existing.credential_key_version = encrypted.key_version
    existing.scopes = grant.scopes
    existing.status = "active"
    existing.token_expires_at = grant.expires_at
    existing.last_verified_at = now
    existing.deleted_at = None
    existing.updated_by = user_id
    await db.commit()
    await db.refresh(existing)
    return existing
