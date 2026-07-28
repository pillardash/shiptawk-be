from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integrations.models import IntegrationConnection
from app.modules.integrations.providers.credential_vault_provider import (
    EncryptedIntegrationCredentials,
)


async def get_workspace_connection(
    db: AsyncSession, workspace_id: UUID, connection_id: UUID
) -> IntegrationConnection | None:
    return cast(
        IntegrationConnection | None,
        await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.id == connection_id,
                IntegrationConnection.deleted_at.is_(None),
            )
        ),
    )


def _current_provider_query(
    workspace_id: UUID, provider: str
) -> Select[tuple[IntegrationConnection]]:
    return (
        select(IntegrationConnection)
        .where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == provider,
            IntegrationConnection.status != "revoked",
            IntegrationConnection.deleted_at.is_(None),
            IntegrationConnection.revoked_at.is_(None),
        )
        .order_by(IntegrationConnection.updated_at.desc(), IntegrationConnection.id.desc())
        .limit(1)
    )


async def get_current_provider_connection(
    db: AsyncSession, workspace_id: UUID, provider: str
) -> IntegrationConnection | None:
    return cast(
        IntegrationConnection | None,
        await db.scalar(_current_provider_query(workspace_id, provider)),
    )


async def lock_current_provider_connection(
    db: AsyncSession, workspace_id: UUID, provider: str
) -> IntegrationConnection | None:
    return cast(
        IntegrationConnection | None,
        await db.scalar(_current_provider_query(workspace_id, provider).with_for_update()),
    )


async def lock_retained_provider_connection(
    db: AsyncSession, workspace_id: UUID, provider: str
) -> IntegrationConnection | None:
    return cast(
        IntegrationConnection | None,
        await db.scalar(
            select(IntegrationConnection)
            .where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.provider == provider,
            )
            .order_by(IntegrationConnection.updated_at.desc(), IntegrationConnection.id.desc())
            .limit(1)
            .with_for_update()
        ),
    )


async def replace_encrypted_credentials(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    connection_id: UUID,
    credentials: EncryptedIntegrationCredentials,
    actor_id: UUID,
) -> bool:
    updated_connection_id = await db.scalar(
        update(IntegrationConnection)
        .where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.id == connection_id,
            IntegrationConnection.status != "revoked",
            IntegrationConnection.deleted_at.is_(None),
            IntegrationConnection.revoked_at.is_(None),
        )
        .values(
            credentials_ciphertext=credentials.ciphertext,
            credential_key_version=credentials.key_version,
            revoked_at=None,
            updated_by=actor_id,
        )
        .returning(IntegrationConnection.id)
    )
    return updated_connection_id is not None


async def revoke_local_credentials(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    connection_id: UUID,
    actor_id: UUID,
    revoked_at: datetime,
    soft_delete: bool = False,
) -> bool:
    updated_connection_id = await db.scalar(
        update(IntegrationConnection)
        .where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.id == connection_id,
            IntegrationConnection.deleted_at.is_(None),
        )
        .values(
            status="revoked",
            credentials_ciphertext=None,
            credential_key_version=None,
            token_expires_at=None,
            revoked_at=revoked_at,
            deleted_at=revoked_at if soft_delete else None,
            updated_by=actor_id,
        )
        .returning(IntegrationConnection.id)
    )
    return updated_connection_id is not None


__all__ = [
    "get_current_provider_connection",
    "get_workspace_connection",
    "lock_current_provider_connection",
    "lock_retained_provider_connection",
    "replace_encrypted_credentials",
    "revoke_local_credentials",
]
