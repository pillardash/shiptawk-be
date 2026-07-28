from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.integrations.models import IntegrationConnection
from app.modules.integrations.providers.x_oauth import (
    IntegrationCredentialCipher,
    XOAuthGrant,
    XOAuthProviderError,
)
from app.modules.integrations.services.oauth_transaction_service import (
    IntegrationOAuthTransactionBinding,
    IntegrationOAuthTransactionService,
)


def _transaction_service() -> IntegrationOAuthTransactionService:
    settings = get_settings()
    key = settings.oauth_transaction_encryption_key
    if key is None:
        raise RuntimeError("OAuth transaction encryption is not configured.")
    return IntegrationOAuthTransactionService(
        encryption_key=key.get_secret_value(),
        expire_after=timedelta(minutes=settings.oauth_transaction_expire_minutes),
    )


async def create_x_oauth_transaction(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    return_path: str,
    redirect_uri: str,
) -> tuple[str, str]:
    created = await _transaction_service().create(
        db,
        binding=IntegrationOAuthTransactionBinding(
            workspace_id=workspace_id,
            actor_id=user_id,
            provider="x",
        ),
        return_path=return_path,
        redirect_uri=redirect_uri,
    )
    return created.state, created.code_verifier


async def consume_x_oauth_transaction(
    db: AsyncSession, *, workspace_id: UUID, user_id: UUID, state: str
) -> tuple[str, str, str]:
    consumed = await _transaction_service().consume(
        db,
        binding=IntegrationOAuthTransactionBinding(
            workspace_id=workspace_id,
            actor_id=user_id,
            provider="x",
        ),
        state=state,
    )
    return consumed.code_verifier, consumed.return_path, consumed.redirect_uri


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
        .values(
            status="revoked",
            credentials_ciphertext=None,
            credential_key_version=None,
            revoked_at=now,
            deleted_at=now,
            updated_by=user_id,
        )
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
    existing.revoked_at = None
    existing.deleted_at = None
    existing.updated_by = user_id
    await db.commit()
    await db.refresh(existing)
    return existing
