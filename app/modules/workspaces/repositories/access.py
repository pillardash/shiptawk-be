from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import WorkspaceMembership


async def get_active_membership_role(
    db: AsyncSession, workspace_id: UUID, user_id: UUID
) -> WorkspaceRole | None:
    return cast(
        WorkspaceRole | None,
        await db.scalar(
            select(WorkspaceMembership.role).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.is_active.is_(True),
            )
        ),
    )


async def has_active_membership(db: AsyncSession, workspace_id: UUID, user_id: UUID) -> bool:
    return await get_active_membership_role(db, workspace_id, user_id) is not None
