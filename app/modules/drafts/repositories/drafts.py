from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.drafts.enums import DraftStatus
from app.modules.drafts.models import drafts, tweet_candidates
from app.modules.operator.models import event_evaluations
from app.modules.repos.models import repos
from app.modules.workspaces.repositories import has_active_membership

draft_response_columns = (
    drafts.c.id,
    drafts.c.workspace_id,
    drafts.c.repo_id,
    repos.c.repo_full_name,
    drafts.c.normalized_event_id,
    drafts.c.content,
    drafts.c.user_edited,
    drafts.c.edit_distance,
    drafts.c.source_event_type,
    drafts.c.status,
    drafts.c.rejection_reason,
    drafts.c.approved_at,
    drafts.c.rejected_at,
    drafts.c.posted_at,
    drafts.c.tweet_id,
    drafts.c.created_at,
    drafts.c.updated_at,
    drafts.c.generation_run_id,
    drafts.c.selected_candidate_id,
)
draft_repo_join = drafts.join(
    repos,
    and_(
        repos.c.id == drafts.c.repo_id,
        repos.c.workspace_id == drafts.c.workspace_id,
    ),
)


async def list_drafts_for_workspace(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    status: DraftStatus | None,
    page: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int] | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None

    filters = [drafts.c.workspace_id == workspace_id]
    if status is not None:
        filters.append(drafts.c.status == status.value)

    total = await db.scalar(select(func.count()).select_from(draft_repo_join).where(*filters))
    rows = (
        await db.execute(
            select(*draft_response_columns)
            .select_from(draft_repo_join)
            .where(*filters)
            .order_by(drafts.c.created_at.desc(), drafts.c.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).mappings()
    return [dict(row) for row in rows], total or 0


async def get_draft_review_for_workspace(
    db: AsyncSession, workspace_id: UUID, draft_id: UUID, user_id: UUID
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]] | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None
    draft = (
        (
            await db.execute(
                select(*draft_response_columns)
                .select_from(draft_repo_join)
                .where(
                    drafts.c.id == draft_id,
                    drafts.c.workspace_id == workspace_id,
                )
            )
        )
        .mappings()
        .first()
    )
    if draft is None:
        return None

    draft_data = dict(draft)
    normalized_event_id = draft_data["normalized_event_id"]
    if normalized_event_id is None:
        return draft_data, None, []

    evaluation = (
        (
            await db.execute(
                select(event_evaluations)
                .where(
                    event_evaluations.c.workspace_id == workspace_id,
                    event_evaluations.c.normalized_event_id == normalized_event_id,
                )
                .order_by(event_evaluations.c.id.asc())
            )
        )
        .mappings()
        .first()
    )
    candidate_filters = [
        tweet_candidates.c.workspace_id == workspace_id,
        tweet_candidates.c.normalized_event_id == normalized_event_id,
    ]
    generation_run_id = draft_data["generation_run_id"]
    if generation_run_id is not None:
        current_count = await db.scalar(
            select(func.count())
            .select_from(tweet_candidates)
            .where(
                *candidate_filters,
                tweet_candidates.c.generation_run_id == generation_run_id,
            )
        )
        if current_count:
            candidate_filters.append(tweet_candidates.c.generation_run_id == generation_run_id)
    candidates = (
        await db.execute(
            select(tweet_candidates)
            .where(*candidate_filters)
            .order_by(tweet_candidates.c.rank.asc(), tweet_candidates.c.id.asc())
        )
    ).mappings()
    return (
        draft_data,
        dict(evaluation) if evaluation is not None else None,
        [dict(candidate) for candidate in candidates],
    )
