from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import func, select

from app.api.deps import DbDep, MutationUserDep, get_current_user
from app.modules.identity.models.users import User
from app.modules.operator.models import (
    ActionRiskReview,
    ApprovalRequest,
    MarketingPlan,
    MeasurementWindow,
    OperatorRecommendation,
    OperatorRun,
    PlanAction,
    PreparedAsset,
)
from app.modules.operator.policies import WRITE_ROLES, require_role
from app.modules.operator.policies.weekly_growth_policy import DomainConflictError
from app.modules.operator.schemas.weekly_growth_api_schema import (
    ActionApprovalCommand,
    ActionProjection,
    AssetDecisionCommand,
    AssetPatchCommand,
    AssetProjection,
    MeasurementProjection,
    PlanProjection,
    ReasonCommand,
    RecommendationProjection,
    RunProjection,
    TodayProjection,
)
from app.modules.operator.services.weekly_growth_service import (
    approve_action_revision_use_case,
    decide_recommendation_use_case,
    reject_asset_use_case,
    revise_asset_use_case,
    transition_action_use_case,
)
from app.modules.products.models import Product
from app.modules.workspaces.services import require_active_workspace_role
from app.shared.exceptions import BadRequestError, ConflictError, NotFoundError

router = APIRouter(
    prefix="/workspaces/{workspace_id}/products/{product_id}/operator",
    tags=["weekly-growth-operator"],
)
CurrentUserDep = Annotated[User, Depends(get_current_user)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


async def _access(db: DbDep, workspace_id: UUID, product_id: UUID, user_id: UUID) -> Product:
    await require_active_workspace_role(
        db,
        workspace_id,
        user_id,
        missing=NotFoundError("Operator resource not found.", code="operator_resource_not_found"),
    )
    product = await db.scalar(
        select(Product).where(Product.workspace_id == workspace_id, Product.id == product_id)
    )
    if product is None:
        raise NotFoundError("Operator resource not found.", code="operator_resource_not_found")
    return product


def _run(row: OperatorRun) -> RunProjection:
    return RunProjection(
        id=row.id,
        run_kind=row.run_kind,
        trigger=row.trigger,
        status=row.status,
        current_stage=row.current_stage,
        stage_summary=dict(row.stage_summary or {}),
        failure_category=row.failure_category,
        period_start=row.logical_period_start,
        period_end=row.logical_period_end,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _action(row: PlanAction) -> ActionProjection:
    return ActionProjection(
        id=row.id,
        plan_id=row.plan_id,
        recommendation_id=row.recommendation_id,
        position=row.position,
        title=row.title,
        description=row.description,
        confidence=row.confidence,
        approval_level=row.approval_level,
        approval_status=row.approval_status,
        status=row.status,
        prepared_output_type=row.prepared_output_type,
        expected_metric=dict(row.expected_metric or {}),
        measurement_due_at=row.measurement_due_at,
        approved_asset_id=row.approved_asset_id,
        approved_asset_revision=row.approved_asset_revision,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


async def _plan(db: DbDep, row: MarketingPlan) -> PlanProjection:
    actions = list(
        await db.scalars(
            select(PlanAction)
            .where(
                PlanAction.workspace_id == row.workspace_id,
                PlanAction.product_id == row.product_id,
                PlanAction.plan_id == row.id,
            )
            .order_by(PlanAction.position, PlanAction.id)
            .limit(3)
        )
    )
    snapshot = dict(row.plan_snapshot or {})
    return PlanProjection(
        id=row.id,
        operator_run_id=row.operator_run_id,
        title=row.title,
        status="ready",
        revision=row.plan_revision or 1,
        period_start=row.period_start,
        period_end=row.period_end,
        finalized_at=row.finalized_at or row.updated_at,
        action_count=row.action_count or len(actions),
        no_recommendation_count=row.no_recommendation_count or 0,
        shipped_summary=_mapping(snapshot.get("shippedSummary")),
        search_summary=_mapping(snapshot.get("searchSummary")),
        actions=[_action(item) for item in actions],
    )


@router.get("/today", response_model=TodayProjection)
async def get_today(
    workspace_id: UUID, product_id: UUID, user: CurrentUserDep, db: DbDep
) -> TodayProjection:
    product = await _access(db, workspace_id, product_id, user.id)
    run = await db.scalar(
        select(OperatorRun)
        .where(
            OperatorRun.workspace_id == workspace_id,
            OperatorRun.product_id == product_id,
            OperatorRun.run_kind == "weekly_growth",
        )
        .order_by(OperatorRun.started_at.desc(), OperatorRun.id.desc())
        .limit(1)
    )
    plan = await db.scalar(
        select(MarketingPlan)
        .where(
            MarketingPlan.workspace_id == workspace_id,
            MarketingPlan.product_id == product_id,
            MarketingPlan.status == "ready",
        )
        .order_by(MarketingPlan.finalized_at.desc(), MarketingPlan.id.desc())
        .limit(1)
    )
    completed = list(
        await db.scalars(
            select(PlanAction)
            .where(
                PlanAction.workspace_id == workspace_id,
                PlanAction.product_id == product_id,
                PlanAction.status == "completed",
            )
            .order_by(PlanAction.completed_at.desc(), PlanAction.id.desc())
            .limit(5)
        )
    )
    count_rows = (
        await db.execute(
            select(PlanAction.approval_status, func.count())
            .where(
                PlanAction.workspace_id == workspace_id,
                PlanAction.product_id == product_id,
            )
            .group_by(PlanAction.approval_status)
        )
    ).all()
    counts: dict[str, int] = {str(key): int(value) for key, value in count_rows}
    projected = await _plan(db, plan) if plan else None
    now = datetime.now(UTC)
    days = (product.operator_weekday - now.weekday()) % 7
    next_schedule = (now + timedelta(days=days)).replace(
        hour=product.operator_hour, minute=0, second=0, microsecond=0
    )
    if next_schedule <= now:
        next_schedule += timedelta(days=7)
    summary = dict(run.stage_summary or {}) if run else {}
    return TodayProjection(
        plan=projected,
        run=_run(run) if run else None,
        source_health=_mapping(summary.get("sourceHealth")),
        next_schedule=next_schedule if product.operator_scheduled_enabled else None,
        approval_counts=counts,
        shipped_summary=projected.shipped_summary if projected else {},
        search_summary=projected.search_summary if projected else {},
        recent_completed=[_action(item) for item in completed],
    )


@router.get("/plans", response_model=list[PlanProjection])
async def list_plans(
    workspace_id: UUID,
    product_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[PlanProjection]:
    await _access(db, workspace_id, product_id, user.id)
    rows = list(
        await db.scalars(
            select(MarketingPlan)
            .where(
                MarketingPlan.workspace_id == workspace_id,
                MarketingPlan.product_id == product_id,
                MarketingPlan.status == "ready",
            )
            .order_by(MarketingPlan.finalized_at.desc(), MarketingPlan.id.desc())
            .limit(limit)
        )
    )
    return [await _plan(db, row) for row in rows]


async def _ready_plan(
    db: DbDep, workspace_id: UUID, product_id: UUID, plan_id: UUID
) -> MarketingPlan:
    row = await db.scalar(
        select(MarketingPlan).where(
            MarketingPlan.workspace_id == workspace_id,
            MarketingPlan.product_id == product_id,
            MarketingPlan.id == plan_id,
            MarketingPlan.status == "ready",
        )
    )
    if row is None:
        raise NotFoundError("Plan not found.", code="plan_not_found")
    return row


@router.get("/plans/{plan_id}", response_model=PlanProjection)
async def get_plan(
    workspace_id: UUID, product_id: UUID, plan_id: UUID, user: CurrentUserDep, db: DbDep
) -> PlanProjection:
    await _access(db, workspace_id, product_id, user.id)
    return await _plan(db, await _ready_plan(db, workspace_id, product_id, plan_id))


@router.get("/runs/{run_id}/plan", response_model=PlanProjection)
async def get_run_plan(
    workspace_id: UUID, product_id: UUID, run_id: UUID, user: CurrentUserDep, db: DbDep
) -> PlanProjection:
    await _access(db, workspace_id, product_id, user.id)
    row = await db.scalar(
        select(MarketingPlan).where(
            MarketingPlan.workspace_id == workspace_id,
            MarketingPlan.product_id == product_id,
            MarketingPlan.operator_run_id == run_id,
            MarketingPlan.status == "ready",
        )
    )
    if row is None:
        raise NotFoundError("Plan not found.", code="plan_not_found")
    return await _plan(db, row)


def _recommendation(row: OperatorRecommendation) -> RecommendationProjection:
    output = dict(row.output)
    return RecommendationProjection(
        id=row.id,
        opportunity_id=row.opportunity_id,
        operator_run_id=row.operator_run_id,
        kind=row.kind,
        title=output.get("title") if isinstance(output.get("title"), str) else None,
        rationale=output.get("rationale") if isinstance(output.get("rationale"), str) else None,
        recommendation=(
            output.get("recommendation") if isinstance(output.get("recommendation"), str) else None
        ),
        confidence=output.get("confidence") if isinstance(output.get("confidence"), str) else None,
        evidence_ids=_strings(output.get("evidenceIds")),
        accepted_at=row.accepted_at,
        created_at=row.created_at,
    )


async def _get_recommendation(
    db: DbDep, workspace_id: UUID, product_id: UUID, recommendation_id: UUID
) -> OperatorRecommendation:
    row = await db.scalar(
        select(OperatorRecommendation).where(
            OperatorRecommendation.workspace_id == workspace_id,
            OperatorRecommendation.product_id == product_id,
            OperatorRecommendation.id == recommendation_id,
        )
    )
    if row is None:
        raise NotFoundError("Recommendation not found.", code="recommendation_not_found")
    return row


@router.get("/recommendations/{recommendation_id}", response_model=RecommendationProjection)
async def get_recommendation(
    workspace_id: UUID,
    product_id: UUID,
    recommendation_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
) -> RecommendationProjection:
    await _access(db, workspace_id, product_id, user.id)
    return _recommendation(
        await _get_recommendation(db, workspace_id, product_id, recommendation_id)
    )


@router.post("/recommendations/{recommendation_id}/accept", response_model=RecommendationProjection)
async def accept_recommendation(
    workspace_id: UUID,
    product_id: UUID,
    recommendation_id: UUID,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> RecommendationProjection:
    await _access(db, workspace_id, product_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    try:
        row = await decide_recommendation_use_case(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            recommendation_id=recommendation_id,
            accept=True,
        )
    except LookupError as exc:
        raise NotFoundError("Recommendation not found.", code="recommendation_not_found") from exc
    return _recommendation(row)


@router.post(
    "/recommendations/{recommendation_id}/dismiss", response_model=RecommendationProjection
)
async def dismiss_recommendation(
    workspace_id: UUID,
    product_id: UUID,
    recommendation_id: UUID,
    payload: ReasonCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> RecommendationProjection:
    await _access(db, workspace_id, product_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    try:
        row = await decide_recommendation_use_case(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            recommendation_id=recommendation_id,
            accept=False,
        )
    except LookupError as exc:
        raise NotFoundError("Recommendation not found.", code="recommendation_not_found") from exc
    return _recommendation(row)


@router.get("/actions", response_model=list[ActionProjection])
async def list_actions(
    workspace_id: UUID, product_id: UUID, user: CurrentUserDep, db: DbDep
) -> list[ActionProjection]:
    await _access(db, workspace_id, product_id, user.id)
    rows = list(
        await db.scalars(
            select(PlanAction)
            .join(MarketingPlan, MarketingPlan.id == PlanAction.plan_id)
            .where(
                PlanAction.workspace_id == workspace_id,
                PlanAction.product_id == product_id,
                MarketingPlan.workspace_id == workspace_id,
                MarketingPlan.product_id == product_id,
                MarketingPlan.status == "ready",
            )
            .order_by(PlanAction.created_at.desc(), PlanAction.position, PlanAction.id)
        )
    )
    return [_action(row) for row in rows]


async def _get_action(
    db: DbDep, workspace_id: UUID, product_id: UUID, action_id: UUID
) -> PlanAction:
    row = await db.scalar(
        select(PlanAction).where(
            PlanAction.workspace_id == workspace_id,
            PlanAction.product_id == product_id,
            PlanAction.id == action_id,
        )
    )
    if row is None:
        raise NotFoundError("Action not found.", code="action_not_found")
    return row


@router.get("/actions/{action_id}", response_model=ActionProjection)
async def get_action(
    workspace_id: UUID, product_id: UUID, action_id: UUID, user: CurrentUserDep, db: DbDep
) -> ActionProjection:
    await _access(db, workspace_id, product_id, user.id)
    return _action(await _get_action(db, workspace_id, product_id, action_id))


@router.post("/actions/{action_id}/approve", response_model=ActionProjection)
async def approve_action(
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    payload: ActionApprovalCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> ActionProjection:
    await _access(db, workspace_id, product_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    try:
        action = await approve_action_revision_use_case(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            action_id=action_id,
            asset_id=payload.asset_id,
            revision=payload.revision,
            actor_id=user.id,
        )
    except LookupError as exc:
        raise NotFoundError(
            "Action or exact asset revision not found.", code="action_not_found"
        ) from exc
    except DomainConflictError as exc:
        raise ConflictError(str(exc), code="stale_asset_revision") from exc
    return _action(action)


async def _transition(
    db: DbDep,
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    actor_id: UUID,
    target: str,
    reason: str | None,
    idempotency_key: str,
) -> PlanAction:
    try:
        return await transition_action_use_case(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            action_id=action_id,
            target=target,
            actor_id=actor_id,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    except LookupError as exc:
        raise NotFoundError("Action not found.", code="action_not_found") from exc
    except DomainConflictError as exc:
        raise ConflictError(str(exc), code="invalid_action_transition") from exc


@router.post("/actions/{action_id}/dismiss", response_model=ActionProjection)
async def dismiss_action(
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    payload: ReasonCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> ActionProjection:
    await _access(db, workspace_id, product_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    return _action(
        await _transition(
            db,
            workspace_id,
            product_id,
            action_id,
            user.id,
            "dismissed",
            payload.reason,
            idempotency_key,
        )
    )


@router.post("/actions/{action_id}/start", response_model=ActionProjection)
async def start_action(
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> ActionProjection:
    await _access(db, workspace_id, product_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    return _action(
        await _transition(
            db, workspace_id, product_id, action_id, user.id, "executing", None, idempotency_key
        )
    )


@router.post("/actions/{action_id}/complete", response_model=ActionProjection)
async def complete_action(
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> ActionProjection:
    await _access(db, workspace_id, product_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    return _action(
        await _transition(
            db, workspace_id, product_id, action_id, user.id, "completed", None, idempotency_key
        )
    )


async def _asset_projection(db: DbDep, row: PreparedAsset) -> AssetProjection:
    review = await db.scalar(
        select(ActionRiskReview).where(
            ActionRiskReview.workspace_id == row.workspace_id,
            ActionRiskReview.product_id == row.product_id,
            ActionRiskReview.asset_id == row.id,
            ActionRiskReview.asset_revision == row.revision,
        )
    )
    approval = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.workspace_id == row.workspace_id,
            ApprovalRequest.product_id == row.product_id,
            ApprovalRequest.asset_id == row.id,
            ApprovalRequest.asset_revision == row.revision,
        )
    )
    return AssetProjection(
        id=row.id,
        recommendation_id=row.recommendation_id,
        action_id=row.action_id,
        revision=row.revision,
        kind=row.kind,
        structured_content=dict(row.structured_content),
        risk_outcome=review.final_outcome if review else None,
        approval_status=approval.status if approval else None,
        created_at=row.created_at,
    )


async def _get_asset(
    db: DbDep, workspace_id: UUID, product_id: UUID, asset_id: UUID
) -> PreparedAsset:
    row = await db.scalar(
        select(PreparedAsset).where(
            PreparedAsset.workspace_id == workspace_id,
            PreparedAsset.product_id == product_id,
            PreparedAsset.id == asset_id,
        )
    )
    if row is None:
        raise NotFoundError("Asset not found.", code="asset_not_found")
    return row


@router.get("/assets/{asset_id}", response_model=AssetProjection)
async def get_asset(
    workspace_id: UUID, product_id: UUID, asset_id: UUID, user: CurrentUserDep, db: DbDep
) -> AssetProjection:
    await _access(db, workspace_id, product_id, user.id)
    return await _asset_projection(db, await _get_asset(db, workspace_id, product_id, asset_id))


@router.get("/assets/{asset_id}/revisions", response_model=list[AssetProjection])
async def list_asset_revisions(
    workspace_id: UUID, product_id: UUID, asset_id: UUID, user: CurrentUserDep, db: DbDep
) -> list[AssetProjection]:
    await _access(db, workspace_id, product_id, user.id)
    root = await _get_asset(db, workspace_id, product_id, asset_id)
    rows = list(
        await db.scalars(
            select(PreparedAsset)
            .where(
                PreparedAsset.workspace_id == workspace_id,
                PreparedAsset.product_id == product_id,
                PreparedAsset.recommendation_id == root.recommendation_id,
                PreparedAsset.action_id == root.action_id,
            )
            .order_by(PreparedAsset.revision.desc())
        )
    )
    return [await _asset_projection(db, row) for row in rows]


@router.patch(
    "/assets/{asset_id}", response_model=AssetProjection, status_code=status.HTTP_201_CREATED
)
async def patch_asset(
    workspace_id: UUID,
    product_id: UUID,
    asset_id: UUID,
    payload: AssetPatchCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> AssetProjection:
    await _access(db, workspace_id, product_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    try:
        new_asset = await revise_asset_use_case(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            asset_id=asset_id,
            revision=payload.revision,
            structured_content=payload.structured_content,
        )
    except DomainConflictError as exc:
        raise ConflictError("Asset revision is stale.", code="stale_asset_revision") from exc
    except ValueError as exc:
        raise BadRequestError(
            "Structured content does not match the asset kind.", code="invalid_asset_content"
        ) from exc
    except LookupError as exc:
        raise NotFoundError("Asset not found.", code="asset_not_found") from exc
    return await _asset_projection(db, new_asset)


@router.post("/assets/{asset_id}/approve", response_model=AssetProjection)
async def approve_asset(
    workspace_id: UUID,
    product_id: UUID,
    asset_id: UUID,
    payload: AssetDecisionCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> AssetProjection:
    await _access(db, workspace_id, product_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    row = await _get_asset(db, workspace_id, product_id, asset_id)
    if row.revision != payload.revision:
        raise ConflictError("Asset revision is stale.", code="stale_asset_revision")
    if row.action_id is None:
        raise ConflictError("Asset is not attached to an action.", code="asset_not_actionable")
    try:
        await approve_action_revision_use_case(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            action_id=row.action_id,
            asset_id=row.id,
            revision=row.revision,
            actor_id=user.id,
        )
    except LookupError as exc:
        raise NotFoundError(
            "Action or exact asset revision not found.", code="action_not_found"
        ) from exc
    except DomainConflictError as exc:
        raise ConflictError(str(exc), code="stale_asset_revision") from exc
    return await _asset_projection(db, row)


@router.post("/assets/{asset_id}/reject", response_model=AssetProjection)
async def reject_asset(
    workspace_id: UUID,
    product_id: UUID,
    asset_id: UUID,
    payload: AssetDecisionCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> AssetProjection:
    await _access(db, workspace_id, product_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    try:
        row = await reject_asset_use_case(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            asset_id=asset_id,
            revision=payload.revision,
            actor_id=user.id,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise NotFoundError(str(exc), code="approval_not_found") from exc
    return await _asset_projection(db, row)


@router.get("/actions/{action_id}/measurement", response_model=MeasurementProjection)
async def get_measurement(
    workspace_id: UUID, product_id: UUID, action_id: UUID, user: CurrentUserDep, db: DbDep
) -> MeasurementProjection:
    await _access(db, workspace_id, product_id, user.id)
    await _get_action(db, workspace_id, product_id, action_id)
    row = await db.scalar(
        select(MeasurementWindow)
        .where(
            MeasurementWindow.workspace_id == workspace_id,
            MeasurementWindow.product_id == product_id,
            MeasurementWindow.action_id == action_id,
        )
        .order_by(MeasurementWindow.created_at.desc())
        .limit(1)
    )
    if row is None:
        raise NotFoundError("Measurement not found.", code="measurement_not_found")
    return MeasurementProjection(
        id=row.id,
        action_id=row.action_id,
        status=row.status,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        due_at=row.due_at,
        expected_metric=dict(row.expected_metric or row.metric_contract),
        baseline=dict(row.baseline),
        result=dict(row.result) if row.result else None,
        outcome=row.outcome,
        limitations=list(row.limitations or []),
    )
