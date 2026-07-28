from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integrations.models import IntegrationConnection
from app.modules.integrations.repositories.integration_connection_repository import (
    revoke_local_credentials,
)


async def list_workspace_connections(
    db: AsyncSession, workspace_id: UUID
) -> list[IntegrationConnection]:
    return list(
        await db.scalars(
            select(IntegrationConnection)
            .where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.deleted_at.is_(None),
            )
            .order_by(IntegrationConnection.created_at, IntegrationConnection.id)
        )
    )


async def get_latest_x_connection(
    db: AsyncSession, workspace_id: UUID
) -> IntegrationConnection | None:
    return cast(
        IntegrationConnection | None,
        await db.scalar(
            select(IntegrationConnection)
            .where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.provider.in_(("x", "twitter")),
                IntegrationConnection.deleted_at.is_(None),
            )
            .order_by(IntegrationConnection.updated_at.desc(), IntegrationConnection.id.desc())
            .limit(1)
        ),
    )


async def revoke_x_connections(
    db: AsyncSession, workspace_id: UUID, actor_id: UUID, revoked_at: datetime
) -> None:
    connection_ids = await db.scalars(
        select(IntegrationConnection.id).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider.in_(("x", "twitter")),
            IntegrationConnection.deleted_at.is_(None),
        )
    )
    for connection_id in connection_ids:
        await revoke_local_credentials(
            db,
            workspace_id=workspace_id,
            connection_id=connection_id,
            actor_id=actor_id,
            revoked_at=revoked_at,
            soft_delete=True,
        )
