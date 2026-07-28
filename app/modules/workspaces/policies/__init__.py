from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.policies.access import require_resolved_workspace_role

PRODUCT_WRITE_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor}


def can_write_products(role: WorkspaceRole) -> bool:
    return role in PRODUCT_WRITE_ROLES


__all__ = ["PRODUCT_WRITE_ROLES", "can_write_products", "require_resolved_workspace_role"]
