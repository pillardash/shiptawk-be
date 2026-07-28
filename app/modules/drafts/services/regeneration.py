from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid5

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.drafts.enums import DraftStatus
from app.modules.drafts.models import drafts
from app.modules.drafts.policies.regeneration import ensure_regeneration_allowed
from app.modules.drafts.policies.review import EDIT_ROLES
from app.modules.drafts.repositories.drafts import draft_repo_join, draft_response_columns
from app.modules.drafts.repositories.tweet_candidate_repository import TweetCandidateRepository
from app.modules.drafts.schemas import DraftAngleRegeneration
from app.modules.drafts.schemas.context import (
    ProductContextInput,
    SafetySettingsInput,
    UserContextInput,
    VoiceProfileInput,
)
from app.modules.drafts.schemas.memory import ContentMemory
from app.modules.drafts.schemas.orchestration import GenerationInput
from app.modules.drafts.services.generation_context_service import GenerationContextBuilder
from app.modules.drafts.services.generation_service import (
    InvalidGenerationOutput,
    TransientGenerationFailure,
    TweetGenerationService,
)
from app.modules.drafts.services.review import (
    _add_feedback,
    _add_status,
    _commit,
    _require_role,
)
from app.modules.identity.models.users import User
from app.modules.integrations.events import NormalizedEvent
from app.modules.integrations.models import normalized_events
from app.modules.llm.repositories.generation_run_repository import GenerationRunRepository
from app.modules.llm.services.llm_execution_service import LLMExecutionService
from app.modules.products.models import Product, product_repositories
from app.modules.repos.models import repos
from app.modules.repos.schemas.generation_context_schema import RepositoryContextInput
from app.shared.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _voice_profile(user: User) -> VoiceProfileInput:
    value = user.voice_profile
    return VoiceProfileInput(
        tone_modes=_strings(value.get("tone_modes")),
        phrases_to_avoid=_strings(value.get("phrases_to_avoid")),
        audience_preference=(
            cast(str, value.get("audience_preference"))
            if isinstance(value.get("audience_preference"), str)
            else None
        ),
        style_samples=_strings(value.get("style_samples")),
    )


def _safety_settings(user: User) -> SafetySettingsInput:
    value = user.safety_settings
    max_drafts = value.get("max_drafts_per_week")
    private_mode = value.get("private_repo_mode")
    return SafetySettingsInput(
        blocked_terms=_strings(value.get("blocked_terms")),
        blocked_topics=_strings(value.get("blocked_topics")),
        ignored_paths=_strings(value.get("ignored_paths")),
        preferred_event_types=_strings(value.get("preferred_event_types"))
        or ["push", "release", "pull_request_merged"],
        max_drafts_per_week=max_drafts if isinstance(max_drafts, int) else 14,
        private_repo_mode=private_mode if private_mode in {"normal", "conservative"} else "normal",
    )


def _product_input(product: Product | None) -> ProductContextInput | None:
    if product is None:
        return None
    return ProductContextInput(
        name=product.name,
        description=product.description,
        target_audience=product.target_audience,
        messaging_angle=product.messaging_angle,
        tone_override=cast(Any, product.tone_override),
        blocked_terms=product.blocked_terms,
        blocked_topics=product.blocked_topics,
        safe_public_boundaries=product.safe_public_boundaries,
        primary_customer_pain=product.primary_customer_pain,
        desired_outcome=product.desired_outcome,
        positioning_statement=product.positioning_statement,
        proof_points=product.proof_points,
        customer_use_cases=product.customer_use_cases,
        content_goal=product.content_goal,
        cta_preference=product.cta_preference,
        founder_story_angle=product.founder_story_angle,
    )


async def regenerate_draft_angle(
    db: AsyncSession,
    workspace_id: UUID,
    draft_id: UUID,
    actor_id: UUID,
    payload: DraftAngleRegeneration,
    llm: LLMExecutionService,
) -> dict[str, Any]:
    await _require_role(
        db,
        workspace_id,
        actor_id,
        EDIT_ROLES,
        forbidden_message="Draft regeneration requires an owner, admin, or editor role.",
        forbidden_code="draft_write_forbidden",
    )
    draft = (
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
    if draft is None:
        raise NotFoundError("Draft not found.", code="draft_not_found")
    draft_data = dict(draft)
    if draft_data["status"] not in {DraftStatus.pending.value, DraftStatus.approved.value}:
        raise ConflictError(
            "Only pending or approved drafts can be regenerated.",
            code="draft_regeneration_invalid_status",
        )
    normalized_event_id = draft_data["normalized_event_id"]
    if normalized_event_id is None:
        raise ConflictError(
            "Draft has no normalized event context.", code="draft_regeneration_missing_event"
        )

    event_row = (
        (
            await db.execute(
                select(normalized_events).where(
                    normalized_events.c.workspace_id == workspace_id,
                    normalized_events.c.id == normalized_event_id,
                    normalized_events.c.repo_id == draft_data["repo_id"],
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    repo_row = (
        (
            await db.execute(
                select(repos).where(
                    repos.c.workspace_id == workspace_id,
                    repos.c.id == draft_data["repo_id"],
                    repos.c.is_tracked.is_(True),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if event_row is None:
        raise NotFoundError("Draft not found.", code="draft_not_found")
    if repo_row is None:
        raise NotFoundError("Draft not found.", code="draft_not_found")
    user = await db.get(User, actor_id)
    if user is None:
        raise NotFoundError("Draft not found.", code="draft_not_found")

    mapping = (
        (
            await db.execute(
                select(product_repositories).where(
                    product_repositories.c.workspace_id == workspace_id,
                    product_repositories.c.repo_id == draft_data["repo_id"],
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    product = await db.get(Product, mapping["product_id"]) if mapping is not None else None
    recent_content = list(
        await db.scalars(
            select(drafts.c.content)
            .where(
                drafts.c.workspace_id == workspace_id,
                drafts.c.repo_id == draft_data["repo_id"],
                drafts.c.status.in_((DraftStatus.approved.value, DraftStatus.posted.value)),
                drafts.c.id != draft_id,
            )
            .order_by(drafts.c.updated_at.desc())
            .limit(8)
        )
    )
    context = GenerationContextBuilder().build(
        user=UserContextInput(
            tone_preference=cast(Any, user.tone_preference),
            voice_profile=_voice_profile(user),
            safety_settings=_safety_settings(user),
        ),
        repo=RepositoryContextInput(
            repo_name=repo_row["repo_name"],
            repo_full_name=repo_row["repo_full_name"],
            description=repo_row["description"],
        ),
        product=_product_input(product),
        repo_role=mapping["repo_role"] if mapping is not None else "unknown",
        is_primary_repo=mapping["is_primary_repo"] if mapping is not None else None,
        repo_role_description=(
            mapping["role_description_override"] or mapping["generated_role_description"]
            if mapping is not None
            else None
        ),
        style_anchors=recent_content[:5],
    )
    event = NormalizedEvent(
        event_type=event_row["event_type"],
        repo_full_name=event_row["repo_full_name"],
        title=event_row["title"],
        description=event_row["description"],
        url=event_row["url"],
        occurred_at=event_row["occurred_at"].isoformat(),
        sha=event_row["sha"],
        branch_name=event_row["branch_name"],
        touched_paths=_strings(event_row["touched_paths"]),
        dedupe_key=event_row["dedupe_key"],
    )
    ensure_regeneration_allowed(event, context, bool(repo_row["private"]))
    stable_key = f"draft-angle:{draft_id}:{payload.idempotency_key}"
    try:
        result = await TweetGenerationService(
            runs=GenerationRunRepository(db),
            candidates=TweetCandidateRepository(db),
            llm=llm,
        ).run(
            GenerationInput(
                workspace_id=workspace_id,
                user_id=actor_id,
                repo_id=draft_data["repo_id"],
                product_id=product.id if product is not None else None,
                normalized_event_id=normalized_event_id,
                stable_key=stable_key,
                correlation_id=stable_key,
                event=event,
                context=context,
                repo_private=bool(repo_row["private"]),
                drafts_created_this_week=0,
                content_memory=ContentMemory(
                    recent_approved_content=recent_content,
                    approved_angles=[],
                ),
                trigger="angle_regeneration",
                requested_angle=payload.angle,
            )
        )
    except TransientGenerationFailure as exc:
        raise ServiceUnavailableError(
            "Draft regeneration is temporarily unavailable.",
            code="generation_provider_transient_failure",
        ) from exc
    except InvalidGenerationOutput as exc:
        raise BadRequestError(
            "Generation provider returned invalid draft candidates.",
            code="invalid_generation_output",
        ) from exc
    if not result.candidates or result.run_id is None:
        raise BadRequestError(
            "No regenerated options were produced.", code="no_generation_candidates"
        )

    current = (
        (
            await db.execute(
                select(drafts).where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
            )
        )
        .mappings()
        .one()
    )
    if current["generation_run_id"] == result.run_id:
        return await _draft_response(db, workspace_id, draft_id)
    top = result.candidates[0]
    candidate_id = uuid5(result.run_id, f"{top.rank}:{top.content}")
    now = datetime.now(UTC)
    await db.execute(
        update(drafts)
        .where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
        .values(
            content=top.content,
            generation_run_id=result.run_id,
            selected_candidate_id=candidate_id,
            user_edited=False,
            edit_distance=0,
            status=DraftStatus.pending.value,
            approved_at=None,
            updated_at=now,
        )
    )
    if draft_data["status"] == DraftStatus.approved.value:
        await _add_status(db, draft_data, actor_id, DraftStatus.pending)
    await _add_feedback(
        db,
        {**draft_data, "generation_run_id": result.run_id},
        actor_id,
        "selected",
        candidate_id=candidate_id,
        previous_candidate_id=draft_data["selected_candidate_id"],
        content_before=draft_data["content"],
        content_after=top.content,
        edit_distance=0,
        metadata={"source": "angle_regeneration", "requestedAngle": payload.angle},
    )
    await _commit(db)
    return await _draft_response(db, workspace_id, draft_id)


async def _draft_response(db: AsyncSession, workspace_id: UUID, draft_id: UUID) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                select(*draft_response_columns)
                .select_from(draft_repo_join)
                .where(drafts.c.workspace_id == workspace_id, drafts.c.id == draft_id)
            )
        )
        .mappings()
        .one()
    )
    return dict(row)
