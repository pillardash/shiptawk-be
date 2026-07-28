from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import Workspace, WorkspaceMembership
from app.modules.workspaces.repositories.access import (
    get_active_membership_role as get_active_membership_role,
)
from app.modules.workspaces.repositories.access import (
    has_active_membership as has_active_membership,
)


async def get_first_active_membership(
    db: AsyncSession, user_id: UUID
) -> WorkspaceMembership | None:
    return (
        await db.scalars(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.is_active.is_(True),
            )
            .order_by(WorkspaceMembership.created_at)
        )
    ).first()


async def get_active_workspace_with_role(
    db: AsyncSession, workspace_id: UUID, user_id: UUID
) -> tuple[Workspace, WorkspaceRole] | None:
    row = (
        await db.execute(
            select(Workspace, WorkspaceMembership.role)
            .join(
                WorkspaceMembership,
                (WorkspaceMembership.workspace_id == Workspace.id)
                & (WorkspaceMembership.user_id == user_id),
            )
            .where(
                Workspace.id == workspace_id,
                Workspace.deleted_at.is_(None),
                WorkspaceMembership.is_active.is_(True),
            )
        )
    ).one_or_none()
    return (row.Workspace, row.role) if row is not None else None
