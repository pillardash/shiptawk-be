from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.drafts.publisher import (
    CredentialDecryptionError,
    CredentialDecryptor,
    Publisher,
    PublisherPermanentError,
    PublisherTransientError,
    PublishRequest,
)
from app.domains.drafts.schemas import DraftStatus
from app.domains.integrations.models import IntegrationConnection
from app.domains.legacy.models import (
    draft_feedback_events,
    draft_status_events,
    drafts,
    tweet_candidates,
)
from app.domains.workspaces.models import WorkspaceMembership, WorkspaceRole
from app.shared.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)

EDIT_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor}
REVIEW_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.reviewer}
PUBLISH_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin}


async def _require_role(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    allowed_roles: set[WorkspaceRole],
    *,
    forbidden_message: str,
    forbidden_code: str,
) -> None:
    role = await db.scalar(
        select(WorkspaceMembership.role).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.is_active.is_(True),
        )
    )
    if role is None:
        raise NotFoundError("Draft not found.", code="draft_not_found")
    if role not in allowed_roles:
        raise ForbiddenError(forbidden_message, code=forbidden_code)


async def _lock_draft(db: AsyncSession, workspace_id: UUID, draft_id: UUID) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                select(drafts)
                .where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFoundError("Draft not found.", code="draft_not_found")
    return dict(row)


def _edit_distance(before: str, after: str) -> int:
    if len(before) < len(after):
        before, after = after, before
    previous = list(range(len(after) + 1))
    for before_index, before_character in enumerate(before, start=1):
        current = [before_index]
        for after_index, after_character in enumerate(after, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[after_index] + 1,
                    previous[after_index - 1] + (before_character != after_character),
                )
            )
        previous = current
    return previous[-1]


async def _add_feedback(
    db: AsyncSession,
    draft: dict[str, Any],
    actor_id: UUID,
    event_type: str,
    *,
    candidate_id: UUID | None = None,
    previous_candidate_id: UUID | None = None,
    content_before: str | None = None,
    content_after: str | None = None,
    edit_distance: int | None = None,
    rejection_reason: str | None = None,
    provider_post_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    await db.execute(
        insert(draft_feedback_events).values(
            id=uuid4(),
            workspace_id=draft["workspace_id"],
            user_id=actor_id,
            repo_id=draft["repo_id"],
            draft_id=draft["id"],
            generation_run_id=draft["generation_run_id"],
            candidate_id=candidate_id,
            event_type=event_type,
            previous_candidate_id=previous_candidate_id,
            content_before=content_before,
            content_after=content_after,
            edit_distance=edit_distance,
            rejection_reason=rejection_reason,
            provider_post_id=provider_post_id,
            metadata=metadata or {},
        )
    )


async def _add_status(
    db: AsyncSession, draft: dict[str, Any], actor_id: UUID, status: DraftStatus
) -> None:
    await db.execute(
        insert(draft_status_events).values(
            id=uuid4(),
            workspace_id=draft["workspace_id"],
            user_id=actor_id,
            repo_id=draft["repo_id"],
            draft_id=draft["id"],
            status=status.value,
        )
    )


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def _current_draft(db: AsyncSession, workspace_id: UUID, draft_id: UUID) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                select(drafts).where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


def _publish_receipt(
    draft: dict[str, Any], idempotency_key: str, url: str, *, already_posted: bool
) -> dict[str, Any]:
    return {
        "draft_id": draft["id"],
        "provider": "x",
        "provider_post_id": draft["tweet_id"],
        "url": url,
        "idempotency_key": idempotency_key,
        "already_posted": already_posted,
    }


async def _existing_publish_url(db: AsyncSession, draft_id: UUID, provider_post_id: str) -> str:
    metadata = await db.scalar(
        select(draft_feedback_events.c.metadata)
        .where(
            draft_feedback_events.c.draft_id == draft_id,
            draft_feedback_events.c.event_type == "posted",
            draft_feedback_events.c.provider_post_id == provider_post_id,
        )
        .order_by(draft_feedback_events.c.occurred_at.desc())
        .limit(1)
    )
    url = metadata.get("url") if isinstance(metadata, dict) else None
    if isinstance(url, str):
        return url
    return f"https://x.com/i/web/status/{provider_post_id}"


async def publish_draft(
    db: AsyncSession,
    workspace_id: UUID,
    draft_id: UUID,
    actor_id: UUID,
    publisher: Publisher,
    credential_decryptor: CredentialDecryptor,
) -> dict[str, Any]:
    await _require_role(
        db,
        workspace_id,
        actor_id,
        PUBLISH_ROLES,
        forbidden_message="Draft publishing requires an owner or admin role.",
        forbidden_code="draft_publish_forbidden",
    )
    draft = await _lock_draft(db, workspace_id, draft_id)
    idempotency_key = f"x:draft:{draft_id}"
    if draft["status"] == DraftStatus.posted.value and draft["tweet_id"]:
        url = await _existing_publish_url(db, draft_id, draft["tweet_id"])
        await _commit(db)
        return _publish_receipt(draft, idempotency_key, url, already_posted=True)
    if draft["status"] != DraftStatus.approved.value or draft["approved_at"] is None:
        await db.rollback()
        raise ConflictError(
            "Draft must be explicitly approved before publishing.",
            code="draft_publish_requires_approval",
        )

    connection = await db.scalar(
        select(IntegrationConnection)
        .where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider.in_(("x", "twitter")),
            IntegrationConnection.status == "active",
            IntegrationConnection.deleted_at.is_(None),
        )
        .order_by(IntegrationConnection.updated_at.desc(), IntegrationConnection.id.desc())
        .limit(1)
    )
    if connection is None:
        await db.rollback()
        raise ConflictError("X account is not connected.", code="x_connection_required")
    if "tweet.write" not in connection.scopes:
        await db.rollback()
        raise ConflictError("X connection does not allow posting.", code="x_write_scope_required")
    try:
        credentials = credential_decryptor.decrypt(
            connection.credentials_ciphertext, connection.credential_key_version
        )
    except CredentialDecryptionError as exc:
        await db.rollback()
        raise ServiceUnavailableError(
            "X credentials are unavailable.", code="x_credentials_unavailable"
        ) from exc

    try:
        result = await publisher.publish(
            PublishRequest(
                content=draft["content"],
                credentials=credentials,
                idempotency_key=idempotency_key,
            )
        )
    except PublisherTransientError as exc:
        await db.rollback()
        raise ServiceUnavailableError(
            "X publishing is temporarily unavailable.", code="publisher_transient_failure"
        ) from exc
    except PublisherPermanentError as exc:
        await db.rollback()
        raise BadRequestError("X rejected the post.", code="publisher_rejected") from exc

    now = datetime.now(UTC)
    await db.execute(
        update(drafts)
        .where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
        .values(
            status=DraftStatus.posted.value,
            posted_at=now,
            tweet_id=result.provider_post_id,
            posting_started_at=None,
            updated_at=now,
        )
    )
    await _add_status(db, draft, actor_id, DraftStatus.posted)
    await _add_feedback(
        db,
        draft,
        actor_id,
        "posted",
        candidate_id=draft["selected_candidate_id"],
        content_after=draft["content"],
        edit_distance=draft["edit_distance"],
        provider_post_id=result.provider_post_id,
        metadata={
            "provider": "x",
            "url": result.url,
            "idempotencyKey": idempotency_key,
        },
    )
    await _commit(db)
    posted = await _current_draft(db, workspace_id, draft_id)
    return _publish_receipt(posted, idempotency_key, result.url, already_posted=False)


async def edit_draft_content(
    db: AsyncSession,
    workspace_id: UUID,
    draft_id: UUID,
    actor_id: UUID,
    content: str,
) -> dict[str, Any]:
    await _require_role(
        db,
        workspace_id,
        actor_id,
        EDIT_ROLES,
        forbidden_message="Draft editing requires an owner, admin, or editor role.",
        forbidden_code="draft_write_forbidden",
    )
    draft = await _lock_draft(db, workspace_id, draft_id)
    if draft["status"] not in {DraftStatus.pending.value, DraftStatus.approved.value}:
        raise ConflictError(
            "Only pending or approved drafts can be edited.", code="draft_edit_invalid_status"
        )
    if draft["content"] == content:
        await _commit(db)
        return draft
    distance = _edit_distance(draft["content"], content)
    now = datetime.now(UTC)
    await db.execute(
        update(drafts)
        .where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
        .values(content=content, user_edited=True, edit_distance=distance, updated_at=now)
    )
    await _add_feedback(
        db,
        draft,
        actor_id,
        "edited",
        candidate_id=draft["selected_candidate_id"],
        previous_candidate_id=draft["selected_candidate_id"],
        content_before=draft["content"],
        content_after=content,
        edit_distance=distance,
    )
    await _commit(db)
    return await _current_draft(db, workspace_id, draft_id)


async def approve_draft(
    db: AsyncSession, workspace_id: UUID, draft_id: UUID, actor_id: UUID
) -> dict[str, Any]:
    await _require_role(
        db,
        workspace_id,
        actor_id,
        REVIEW_ROLES,
        forbidden_message="Draft approval requires an owner, admin, or reviewer role.",
        forbidden_code="draft_review_forbidden",
    )
    draft = await _lock_draft(db, workspace_id, draft_id)
    if draft["status"] in {DraftStatus.approved.value, DraftStatus.posted.value}:
        await _commit(db)
        return draft
    if draft["status"] != DraftStatus.pending.value:
        raise ConflictError(
            "Only pending drafts can be approved.", code="draft_approve_invalid_status"
        )
    now = datetime.now(UTC)
    await db.execute(
        update(drafts)
        .where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
        .values(
            status=DraftStatus.approved.value,
            approved_at=now,
            rejected_at=None,
            rejection_reason=None,
            updated_at=now,
        )
    )
    await _add_status(db, draft, actor_id, DraftStatus.approved)
    await _add_feedback(
        db,
        draft,
        actor_id,
        "approved",
        candidate_id=draft["selected_candidate_id"],
        content_after=draft["content"],
        edit_distance=draft["edit_distance"],
    )
    await _commit(db)
    return await _current_draft(db, workspace_id, draft_id)


async def reject_draft(
    db: AsyncSession,
    workspace_id: UUID,
    draft_id: UUID,
    actor_id: UUID,
    reason: str,
) -> dict[str, Any]:
    await _require_role(
        db,
        workspace_id,
        actor_id,
        REVIEW_ROLES,
        forbidden_message="Draft rejection requires an owner, admin, or reviewer role.",
        forbidden_code="draft_review_forbidden",
    )
    draft = await _lock_draft(db, workspace_id, draft_id)
    if draft["status"] == DraftStatus.rejected.value:
        await _commit(db)
        return draft
    if draft["status"] not in {DraftStatus.pending.value, DraftStatus.approved.value}:
        raise ConflictError(
            "Only pending or approved drafts can be rejected.", code="draft_reject_invalid_status"
        )
    now = datetime.now(UTC)
    await db.execute(
        update(drafts)
        .where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
        .values(
            status=DraftStatus.rejected.value,
            approved_at=None,
            rejected_at=now,
            rejection_reason=reason,
            updated_at=now,
        )
    )
    await _add_status(db, draft, actor_id, DraftStatus.rejected)
    await _add_feedback(
        db,
        draft,
        actor_id,
        "rejected",
        candidate_id=draft["selected_candidate_id"],
        content_before=draft["content"],
        edit_distance=draft["edit_distance"],
        rejection_reason=reason,
    )
    await _commit(db)
    return await _current_draft(db, workspace_id, draft_id)


async def select_draft_candidate(
    db: AsyncSession,
    workspace_id: UUID,
    draft_id: UUID,
    actor_id: UUID,
    candidate_id: UUID,
) -> dict[str, Any]:
    await _require_role(
        db,
        workspace_id,
        actor_id,
        EDIT_ROLES,
        forbidden_message="Draft editing requires an owner, admin, or editor role.",
        forbidden_code="draft_write_forbidden",
    )
    draft = await _lock_draft(db, workspace_id, draft_id)
    if draft["status"] not in {DraftStatus.pending.value, DraftStatus.approved.value}:
        raise ConflictError(
            "Only pending or approved drafts can select candidates.",
            code="draft_selection_invalid_status",
        )
    candidate = (
        (
            await db.execute(
                select(tweet_candidates).where(
                    tweet_candidates.c.workspace_id == workspace_id,
                    tweet_candidates.c.id == candidate_id,
                    tweet_candidates.c.normalized_event_id == draft["normalized_event_id"],
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if candidate is None:
        raise NotFoundError("Draft candidate not found.", code="draft_candidate_not_found")
    if draft["selected_candidate_id"] == candidate_id and draft["content"] == candidate["content"]:
        await _commit(db)
        return draft
    now = datetime.now(UTC)
    await db.execute(
        update(drafts)
        .where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
        .values(
            selected_candidate_id=candidate_id,
            content=candidate["content"],
            user_edited=False,
            edit_distance=0,
            updated_at=now,
        )
    )
    await _add_feedback(
        db,
        draft,
        actor_id,
        "selected",
        candidate_id=candidate_id,
        previous_candidate_id=draft["selected_candidate_id"],
        content_before=draft["content"],
        content_after=candidate["content"],
        edit_distance=0,
        metadata={"source": "manual_candidate_switch"},
    )
    await _commit(db)
    return await _current_draft(db, workspace_id, draft_id)
