from app.modules.workspaces.enums import WorkspaceRole
from app.shared.exceptions import AppError


def require_resolved_workspace_role(
    role: WorkspaceRole | None, *, missing: AppError
) -> WorkspaceRole:
    if role is None:
        raise missing
    return role
