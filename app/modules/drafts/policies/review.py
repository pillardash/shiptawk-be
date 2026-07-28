from datetime import datetime

from app.modules.drafts.enums import DraftStatus
from app.modules.workspaces.models import WorkspaceRole
from app.shared.exceptions import ConflictError, ForbiddenError

EDIT_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor}
REVIEW_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.reviewer}
PUBLISH_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin}


def ensure_role(
    role: WorkspaceRole,
    allowed_roles: set[WorkspaceRole],
    *,
    forbidden_message: str,
    forbidden_code: str,
) -> None:
    if role not in allowed_roles:
        raise ForbiddenError(forbidden_message, code=forbidden_code)


def ensure_publishable(status: str, approved_at: datetime | None) -> None:
    if status != DraftStatus.approved.value or approved_at is None:
        raise ConflictError(
            "Draft must be explicitly approved before publishing.",
            code="draft_publish_requires_approval",
        )


def ensure_editable(status: str) -> None:
    if status not in {DraftStatus.pending.value, DraftStatus.approved.value}:
        raise ConflictError(
            "Only pending or approved drafts can be edited.", code="draft_edit_invalid_status"
        )


def ensure_approvable(status: str) -> None:
    if status != DraftStatus.pending.value:
        raise ConflictError(
            "Only pending drafts can be approved.", code="draft_approve_invalid_status"
        )


def ensure_rejectable(status: str) -> None:
    if status not in {DraftStatus.pending.value, DraftStatus.approved.value}:
        raise ConflictError(
            "Only pending or approved drafts can be rejected.", code="draft_reject_invalid_status"
        )


def ensure_candidate_selectable(status: str) -> None:
    if status not in {DraftStatus.pending.value, DraftStatus.approved.value}:
        raise ConflictError(
            "Only pending or approved drafts can select candidates.",
            code="draft_selection_invalid_status",
        )
