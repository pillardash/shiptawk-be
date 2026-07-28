from app.modules.workspaces.models import WorkspaceRole

WRITE_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor}
REVIEW_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.reviewer}


def can_create_evidence(role: WorkspaceRole) -> bool:
    return role in WRITE_ROLES


def can_review_evidence(role: WorkspaceRole) -> bool:
    return role in REVIEW_ROLES


def should_redact_evidence(role: WorkspaceRole | None, classification: str) -> bool:
    return role == WorkspaceRole.viewer and classification in {"confidential", "blocked"}
