from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.services import require_active_workspace_role
from app.shared.exceptions import ForbiddenError, NotFoundError

WRITE_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor}
REVIEW_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.reviewer}


async def require_role(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, allowed: set[WorkspaceRole]
) -> None:
    role = await require_active_workspace_role(
        db,
        workspace_id,
        user_id,
        missing=NotFoundError("Operator resource not found.", code="operator_resource_not_found"),
    )
    if role not in allowed:
        raise ForbiddenError(
            "The workspace role cannot perform this operator action.",
            code="operator_write_forbidden",
        )
