import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.drafts.enums import DraftStatus
from app.modules.drafts.models import (
    draft_feedback_events,
    draft_status_events,
    drafts,
    publication_attempts,
    tweet_candidates,
)
from app.modules.drafts.policies.review import (
    EDIT_ROLES,
    PUBLISH_ROLES,
    REVIEW_ROLES,
    ensure_approvable,
    ensure_candidate_selectable,
    ensure_editable,
    ensure_publishable,
    ensure_rejectable,
    ensure_role,
)
from app.modules.drafts.providers.protocols import (
    CredentialDecryptionError,
    CredentialDecryptor,
    Publisher,
    PublisherPermanentError,
    PublisherTransientError,
    PublishRequest,
)
from app.modules.integrations.models import IntegrationConnection
from app.modules.integrations.providers.credential_vault_provider import CredentialVaultError
from app.modules.operator.models import PreparedAsset
from app.modules.operator.schemas.ai_output_schema import XDraft
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.services import require_active_workspace_role
from app.shared.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)


async def _require_role(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    allowed_roles: set[WorkspaceRole],
    *,
    forbidden_message: str,
    forbidden_code: str,
) -> None:
    role = await require_active_workspace_role(
        db,
        workspace_id,
        user_id,
        missing=NotFoundError("Draft not found.", code="draft_not_found"),
    )
    ensure_role(
        role,
        allowed_roles,
        forbidden_message=forbidden_message,
        forbidden_code=forbidden_code,
    )


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
    try:
        ensure_publishable(draft["status"], draft["approved_at"])
    except ConflictError:
        await db.rollback()
        raise

    attempt = (
        (
            await db.execute(
                select(publication_attempts).where(
                    publication_attempts.c.workspace_id == workspace_id,
                    publication_attempts.c.command_id == idempotency_key,
                )
            )
        )
        .mappings()
        .first()
    )
    if attempt is not None and attempt["status"] == "unknown_outcome":
        await db.rollback()
        raise ConflictError(
            "The previous X publication outcome is unknown and requires reconciliation.",
            code="publisher_unknown_outcome",
        )
    if attempt is None:
        await db.execute(
            insert(publication_attempts).values(
                id=uuid4(),
                workspace_id=workspace_id,
                draft_id=draft_id,
                command_id=idempotency_key,
                status="pending",
                provider="x",
            )
        )
        await db.commit()

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
    if connection.credentials_ciphertext is None or connection.credential_key_version is None:
        await db.rollback()
        raise ServiceUnavailableError(
            "X credentials are unavailable.", code="x_credentials_unavailable"
        )
    try:
        credentials = credential_decryptor.decrypt(
            connection.credentials_ciphertext, connection.credential_key_version
        )
    except (CredentialDecryptionError, CredentialVaultError) as exc:
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
        await db.execute(
            update(publication_attempts)
            .where(
                publication_attempts.c.workspace_id == workspace_id,
                publication_attempts.c.command_id == idempotency_key,
            )
            .values(status="unknown_outcome", error_category="transport")
        )
        await db.commit()
        raise ServiceUnavailableError(
            "X publishing outcome is unknown and requires reconciliation.",
            code="publisher_transient_failure",
        ) from exc
    except PublisherPermanentError as exc:
        await db.execute(
            update(publication_attempts)
            .where(
                publication_attempts.c.workspace_id == workspace_id,
                publication_attempts.c.command_id == idempotency_key,
            )
            .values(status="failed", error_category="provider_rejected")
        )
        await db.commit()
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
    await db.execute(
        update(publication_attempts)
        .where(
            publication_attempts.c.workspace_id == workspace_id,
            publication_attempts.c.command_id == idempotency_key,
        )
        .values(
            status="completed",
            provider_receipt={"providerPostId": result.provider_post_id, "url": result.url},
            completed_at=now,
        )
    )
    await _commit(db)
    posted = await _current_draft(db, workspace_id, draft_id)
    return _publish_receipt(posted, idempotency_key, result.url, already_posted=False)


def _publication_fingerprint(draft_id: UUID, asset_id: UUID, revision: int) -> str:
    payload = json.dumps(
        {"assetId": str(asset_id), "draftId": str(draft_id), "revision": revision},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _publication_command_result(
    *,
    draft_id: UUID,
    asset_id: UUID,
    revision: int,
    idempotency_key: str,
    original_outcome: Literal["success", "failed", "unknown"],
    replay: bool = False,
    receipt: Mapping[str, object] | None = None,
    error_code: str | None = None,
) -> dict[str, object]:
    receipt = receipt or {}
    return {
        "draft_id": draft_id,
        "asset_id": asset_id,
        "revision": revision,
        "outcome": "replay" if replay else original_outcome,
        "original_outcome": original_outcome,
        "provider": "x",
        "provider_post_id": receipt.get("providerPostId"),
        "url": receipt.get("url"),
        "idempotency_key": idempotency_key,
        "error_code": error_code,
    }


async def approve_and_publish_draft_revision(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    draft_id: UUID,
    asset_id: UUID,
    revision: int,
    actor_id: UUID,
    idempotency_key: str,
    publisher: Publisher,
    credential_decryptor: CredentialDecryptor,
) -> dict[str, object]:
    """Approve and publish one immutable operator X asset through the draft authority."""
    await _require_role(
        db,
        workspace_id,
        actor_id,
        PUBLISH_ROLES,
        forbidden_message="Draft publishing requires an owner or admin role.",
        forbidden_code="draft_publish_forbidden",
    )
    key = idempotency_key.strip()
    fingerprint = _publication_fingerprint(draft_id, asset_id, revision)
    attempt = (
        (
            await db.execute(
                select(publication_attempts).where(
                    publication_attempts.c.workspace_id == workspace_id,
                    publication_attempts.c.command_id == key,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if attempt is not None:
        if attempt["payload_fingerprint"] != fingerprint:
            raise ConflictError(
                "Idempotency key was used with another publication payload.",
                code="publication_idempotency_conflict",
            )
        status = attempt["status"]
        original: Literal["success", "failed", "unknown"] = (
            "success" if status == "completed" else "failed" if status == "failed" else "unknown"
        )
        return _publication_command_result(
            draft_id=draft_id,
            asset_id=asset_id,
            revision=revision,
            idempotency_key=key,
            original_outcome=original,
            replay=True,
            receipt=attempt["provider_receipt"],
            error_code=attempt["error_category"],
        )

    draft = await _lock_draft(db, workspace_id, draft_id)
    asset = await db.scalar(
        select(PreparedAsset).where(
            PreparedAsset.workspace_id == workspace_id,
            PreparedAsset.id == asset_id,
            PreparedAsset.revision == revision,
            PreparedAsset.kind == "x_draft",
        )
    )
    try:
        asset_content = (
            TypeAdapter(XDraft).validate_python(asset.structured_content).content.post
            if asset is not None
            else None
        )
    except ValidationError:
        asset_content = None
    if (
        asset is None
        or draft["prepared_asset_id"] != asset_id
        or draft["prepared_asset_revision"] != revision
        or draft["content"] != asset_content
    ):
        await db.rollback()
        raise ConflictError(
            "Draft does not match the requested exact X asset revision.",
            code="draft_revision_conflict",
        )
    if draft["status"] == DraftStatus.posted.value:
        await db.rollback()
        raise ConflictError(
            "This exact draft revision was already published by another command.",
            code="draft_already_published",
        )
    if draft["status"] != DraftStatus.approved.value:
        ensure_approvable(draft["status"])
        now = datetime.now(UTC)
        await db.execute(
            update(drafts)
            .where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
            .values(status=DraftStatus.approved.value, approved_at=now, updated_at=now)
        )
        await _add_status(db, draft, actor_id, DraftStatus.approved)
        await _add_feedback(db, draft, actor_id, "approved", content_after=draft["content"])

    await db.execute(
        insert(publication_attempts).values(
            id=uuid4(),
            workspace_id=workspace_id,
            draft_id=draft_id,
            command_id=key,
            payload_fingerprint=fingerprint,
            status="pending",
            provider="x",
        )
    )
    await _commit(db)

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
    error_code: str | None = None
    credentials: Any = None
    if connection is None:
        error_code = "x_connection_required"
    elif "tweet.write" not in connection.scopes:
        error_code = "x_write_scope_required"
    elif connection.credentials_ciphertext is None or connection.credential_key_version is None:
        error_code = "x_credentials_unavailable"
    else:
        try:
            credentials = credential_decryptor.decrypt(
                connection.credentials_ciphertext, connection.credential_key_version
            )
        except (CredentialDecryptionError, CredentialVaultError):
            error_code = "x_credentials_unavailable"
    if error_code is not None:
        await db.execute(
            update(publication_attempts)
            .where(
                publication_attempts.c.workspace_id == workspace_id,
                publication_attempts.c.command_id == key,
            )
            .values(status="failed", error_category=error_code, completed_at=datetime.now(UTC))
        )
        await _commit(db)
        return _publication_command_result(
            draft_id=draft_id,
            asset_id=asset_id,
            revision=revision,
            idempotency_key=key,
            original_outcome="failed",
            error_code=error_code,
        )

    provider_key = f"x:publication:{workspace_id}:{hashlib.sha256(key.encode()).hexdigest()}"
    try:
        result = await publisher.publish(
            PublishRequest(
                content=draft["content"], credentials=credentials, idempotency_key=provider_key
            )
        )
    except PublisherTransientError:
        await db.execute(
            update(publication_attempts)
            .where(
                publication_attempts.c.workspace_id == workspace_id,
                publication_attempts.c.command_id == key,
            )
            .values(status="unknown_outcome", error_category="publisher_unknown_outcome")
        )
        await _commit(db)
        return _publication_command_result(
            draft_id=draft_id,
            asset_id=asset_id,
            revision=revision,
            idempotency_key=key,
            original_outcome="unknown",
            error_code="publisher_unknown_outcome",
        )
    except PublisherPermanentError:
        await db.execute(
            update(publication_attempts)
            .where(
                publication_attempts.c.workspace_id == workspace_id,
                publication_attempts.c.command_id == key,
            )
            .values(
                status="failed",
                error_category="publisher_rejected",
                completed_at=datetime.now(UTC),
            )
        )
        await _commit(db)
        return _publication_command_result(
            draft_id=draft_id,
            asset_id=asset_id,
            revision=revision,
            idempotency_key=key,
            original_outcome="failed",
            error_code="publisher_rejected",
        )

    now = datetime.now(UTC)
    receipt = {"providerPostId": result.provider_post_id, "url": result.url}
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
        content_after=draft["content"],
        provider_post_id=result.provider_post_id,
        metadata={"provider": "x", "url": result.url, "idempotencyKey": key},
    )
    await db.execute(
        update(publication_attempts)
        .where(
            publication_attempts.c.workspace_id == workspace_id,
            publication_attempts.c.command_id == key,
        )
        .values(status="completed", provider_receipt=receipt, completed_at=now)
    )
    await _commit(db)
    return _publication_command_result(
        draft_id=draft_id,
        asset_id=asset_id,
        revision=revision,
        idempotency_key=key,
        original_outcome="success",
        receipt=receipt,
    )


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
    ensure_editable(draft["status"])
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
    ensure_approvable(draft["status"])
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
    ensure_rejectable(draft["status"])
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
    ensure_candidate_selectable(draft["status"])
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
