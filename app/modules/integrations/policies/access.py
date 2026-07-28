from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.services import require_active_workspace_role
from app.shared.exceptions import ForbiddenError


async def workspace_role(db: AsyncSession, workspace_id: UUID, user_id: UUID) -> WorkspaceRole:
    return await require_active_workspace_role(db, workspace_id, user_id)


async def require_repository_write_role(
    db: AsyncSession, workspace_id: UUID, user_id: UUID
) -> None:
    role = await workspace_role(db, workspace_id, user_id)
    if role not in {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor}:
        raise ForbiddenError("Workspace write access required.", code="workspace_write_forbidden")
