from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.policies.access import require_resolved_workspace_role
from app.modules.workspaces.repositories import get_active_membership_role
from app.shared.exceptions import AppError, ForbiddenError


async def resolve_active_workspace_role(
    db: AsyncSession, workspace_id: UUID, user_id: UUID
) -> WorkspaceRole | None:
    return await get_active_membership_role(db, workspace_id, user_id)


async def require_active_workspace_role(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    *,
    missing: AppError | None = None,
) -> WorkspaceRole:
    role = await resolve_active_workspace_role(db, workspace_id, user_id)
    return require_resolved_workspace_role(
        role,
        missing=missing
        or ForbiddenError("Workspace access denied.", code="workspace_access_denied"),
    )
