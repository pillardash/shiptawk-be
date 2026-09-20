from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy import func, select

from app.api.deps import DbDep, MutationUserDep, get_current_user
from app.modules.drafts.models import drafts
from app.modules.identity.models.users import User
from app.modules.operator.models import (
    ActionRiskReview,
    ApprovalRequest,
    MarketingPlan,
    MeasurementWindow,
    OperatorRecommendation,
    OperatorRecommendationDecision,
    OperatorRun,
    PlanAction,
    PreparedAsset,
)
from app.modules.operator.policies import WRITE_ROLES, require_role
from app.modules.operator.policies.weekly_growth_policy import DomainConflictError
from app.modules.operator.repositories.recommendation_feedback_repository import get_actor_feedback
from app.modules.operator.schemas.weekly_growth_api_schema import (
    ActionApprovalCommand,
    ActionListResponse,
    ActionProjection,
    ActionStatus,
    ApprovalCounts,
    AssetDecisionCommand,
    AssetKind,
    AssetListResponse,
    AssetPatchCommand,
    AssetProjection,
    CorrectiveActionProjection,
    MeasurementListResponse,
    MeasurementProjection,
    NoRecommendationOutcomeProjection,
    OperatorConfigPatch,
    OperatorReadinessProjection,
    PageInfo,
    PlanListResponse,
    PlanProjection,
    PlanViewProjection,
    PreservedState,
    ReasonCommand,
    RecommendationDecisionProjection,
    RecommendationProjection,
    RecommendationUsefulnessFeedbackCommand,
    RecommendationUsefulnessFeedbackProjection,
    RecoveryCommand,
    RecoverySource,
    RunProjection,
    SourceHealthProjection,
    SourceHealthStatus,
    SourceRecoveryProjection,
    SummaryProjection,
    TodayProjection,
    XAssetBridgeProjection,
)
from app.modules.operator.services.canonical_plan_service import resolve_canonical_plan
from app.modules.operator.services.plan_view_service import record_plan_view
from app.modules.operator.services.recommendation_feedback_service import (
    replace_recommendation_feedback,
)
from app.modules.operator.services.weekly_growth_service import (
    CommandIdempotencyConflictError,
    approve_action_revision_use_case,
    command_fingerprint,
    decide_recommendation_use_case,
    reject_asset_use_case,
    revise_asset_use_case,
    transition_action_use_case,
    update_operator_config,
)
from app.modules.products.models import Product
from app.modules.products.services.activation_service import (
    projected_next_schedule,
    read_activation_state,
)
from app.modules.workspaces.services import require_active_workspace_role
from app.shared.exceptions import BadRequestError, ConflictError, NotFoundError

router = APIRouter(
    prefix="/workspaces/{workspace_id}/products/{product_id}/operator",
    tags=["weekly-growth-operator"],
)
CurrentUserDep = Annotated[User, Depends(get_current_user)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


def _source_recovery(
    source: RecoverySource, status: SourceHealthStatus
) -> SourceRecoveryProjection:
    settings_href = "/users/settings/connections"
    command = RecoveryCommand.inspect_source
    if source == RecoverySource.github_installation:
        command = RecoveryCommand.attach_github
    elif source == RecoverySource.github_monitoring:
        command = RecoveryCommand.enable_monitoring
    elif source == RecoverySource.website:
        command = RecoveryCommand.retry_website
    elif source == RecoverySource.search_console:
        command = (
            RecoveryCommand.reconnect_search
            if status == SourceHealthStatus.revoked
            else RecoveryCommand.retry_search_sync
        )
    return SourceRecoveryProjection(
        source=source,
        status=status,
        code=f"{source.value}_{status.value}",
        retryable=status not in {SourceHealthStatus.healthy, SourceHealthStatus.syncing},
        preserved_state=[
            PreservedState.product_profile,
            PreservedState.prior_reports,
            PreservedState.prepared_assets,
            PreservedState.approvals,
            PreservedState.source_configuration,
        ],
        affected_capability=f"{source.value}_evidence",
        recovery_command=command,
        href=settings_href,
    )


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


@router.get("/readiness", response_model=OperatorReadinessProjection)
async def get_operator_readiness(
    workspace_id: UUID,
    product_id: UUID,
    request: Request,
    user: CurrentUserDep,
    db: DbDep,
) -> OperatorReadinessProjection:
    product = await _access(db, workspace_id, product_id, user.id)
    state = await read_activation_state(db, workspace_id)
    if state.product is None or state.product.id != product_id:
        blockers = ["activation_product_mismatch"]
    else:
        blockers = state.run_blockers
    active_run_id = state.active_run.id if state.active_run else None
    profile = state.approved_profile
    website_status = (
        "healthy"
        if state.website_ready
        else "not_connected"
        if state.website_source is None
        else "stale"
    )
    search_status = (
        "healthy"
        if state.search_connected
        else "reduced"
        if product.search_mode == "deferred_reduced"
        else "not_connected"
    )
    recovery: list[SourceRecoveryProjection] = []
    repository_required = product.repository_evidence_mode == "connected"
    if repository_required and not state.github_installed:
        recovery.append(
            SourceRecoveryProjection(
                source=RecoverySource.github_installation,
                status=SourceHealthStatus.not_connected,
                code="github_installation_required",
                retryable=True,
                preserved_state=[PreservedState.product_profile, PreservedState.prior_reports],
                affected_capability="repository_evidence",
                recovery_command=RecoveryCommand.attach_github,
                href="/users/settings/connections",
            )
        )
    if repository_required and not state.monitored_repository_count:
        recovery.append(
            SourceRecoveryProjection(
                source=RecoverySource.github_monitoring,
                status=SourceHealthStatus.not_connected,
                code="monitored_product_repository_required",
                retryable=True,
                preserved_state=[PreservedState.product_profile, PreservedState.prior_reports],
                affected_capability="repository_evidence",
                recovery_command=RecoveryCommand.enable_monitoring,
                href=f"/users/products/{product_id}/repositories",
            )
        )
    if not state.website_ready:
        recovery.append(
            SourceRecoveryProjection(
                source=RecoverySource.website,
                status=SourceHealthStatus.not_connected
                if state.website_source is None
                else SourceHealthStatus.stale,
                code="website_source_required"
                if state.website_source is None
                else "successful_website_crawl_required",
                observed_at=state.website_source.updated_at if state.website_source else None,
                last_successful_at=(
                    state.website_source.last_successful_crawl_at if state.website_source else None
                ),
                retryable=True,
                preserved_state=[PreservedState.product_profile, PreservedState.prior_reports],
                affected_capability="website_evidence",
                recovery_command=RecoveryCommand.retry_website,
                href=f"/users/products/{product_id}/website",
            )
        )
    if not state.search_ready:
        recovery.append(
            SourceRecoveryProjection(
                source=RecoverySource.search_console,
                status=SourceHealthStatus.not_connected,
                code="search_connection_or_explicit_reduced_mode_required",
                observed_at=state.search_source.selected_at if state.search_source else None,
                retryable=True,
                preserved_state=[PreservedState.product_profile, PreservedState.prior_reports],
                affected_capability="search_evidence",
                recovery_command=RecoveryCommand.reconnect_search,
                href=f"/users/products/{product_id}/search",
            )
        )
    return OperatorReadinessProjection(
        product_id=product_id,
        operator_enabled=product.weekly_growth_operator_enabled,
        manual_enabled=product.operator_manual_enabled,
        scheduled_enabled=product.operator_scheduled_enabled,
        email_enabled=product.operator_email_enabled,
        timezone=product.operator_timezone,
        weekday=product.operator_weekday,
        hour=product.operator_hour,
        approved_profile_id=profile.id if profile else None,
        approved_profile_revision=profile.version if profile else None,
        website_readiness={"status": website_status, "ready": state.website_ready},
        search_readiness={"status": search_status, "ready": state.search_ready},
        search_reduced_mode=product.search_mode == "deferred_reduced",
        search_mode=product.search_mode,
        repository_evidence_ready=bool(state.monitored_repository_count),
        evidence_mode=(
            "website_search"
            if product.repository_evidence_mode == "deferred"
            else "website_search_github"
            if state.search_connected
            else "website_github"
            if product.repository_evidence_mode == "connected"
            else None
        ),
        optional_enhancements=(
            ["Add shipping evidence"] if product.repository_evidence_mode == "deferred" else []
        ),
        manual_run_available=not blockers and active_run_id is None,
        blockers=blockers + (["active_operator_run"] if active_run_id else []),
        active_run_id=active_run_id,
        next_schedule=projected_next_schedule(product),
        source_recovery=recovery,
    )


def _run(row: OperatorRun) -> RunProjection:
    corrective = None
    if row.status in {"failed", "stopped"}:
        reason = row.stopped_reason or row.failure_category or "operator_run_failed"
        if "missing_approved_profile" in reason:
            corrective = CorrectiveActionProjection(
                reason="Approved product details are required before this report can run.",
                href=f"/users/products/{row.product_id}/profile",
            )
        else:
            corrective = CorrectiveActionProjection(
                reason=reason.replace("_", " "), href="/users/settings/connections"
            )
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
        stopped_reason=row.stopped_reason,
        corrective_action=corrective,
    )


def _action(row: PlanAction) -> ActionProjection:
    valid_commands: list[str] = []
    if row.status == "pending":
        valid_commands.extend(["approve_asset_revision", "dismiss"])
    elif row.status == "ready":
        valid_commands.extend(["start", "dismiss"])
    elif row.status == "executing":
        valid_commands.append("complete")
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
        dismissal_reason=row.dismissal_reason,
        dismissal_comment=row.dismissal_comment,
        dismissed_by_actor_id=row.dismissed_by_actor_id,
        dismissed_at=row.dismissed_at,
        created_at=row.created_at,
        completed_at=row.completed_at,
        product_id=row.product_id,
        valid_commands=valid_commands,
    )


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _summary_items(value: object) -> list[str]:
    return _strings(value.get("items")) if isinstance(value, dict) else _strings(value)


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
    workspace_id: UUID,
    product_id: UUID,
    request: Request,
    user: CurrentUserDep,
    db: DbDep,
) -> TodayProjection:
    product = await _access(db, workspace_id, product_id, user.id)
    canonical = await resolve_canonical_plan(db, workspace_id=workspace_id, product_id=product_id)
    run, plan = canonical.run, canonical.plan
    readiness = await get_operator_readiness(workspace_id, product_id, request, user, db)
    activation_state = await read_activation_state(db, workspace_id)
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
    counts = {str(key): int(value) for key, value in count_rows}
    projected = await _plan(db, plan) if plan else None
    summary = dict(run.stage_summary or {}) if run else {}
    source = _mapping(summary.get("sourceHealth"))

    def health(key: str) -> SourceHealthStatus:
        value = source.get(key, source.get("search") if key == "searchConsole" else None)
        try:
            return SourceHealthStatus(str(value))
        except ValueError:
            return SourceHealthStatus.not_connected

    plan_snapshot = dict(plan.plan_snapshot or {}) if plan else {}
    raw_no_recommendations = plan_snapshot.get("noRecommendationOutcomes", [])
    no_recommendations = (
        [
            NoRecommendationOutcomeProjection(
                reason=str(item.get("reason", "insufficient_evidence")),
                evidence_ids=_strings(item.get("evidenceIds")),
            )
            for item in raw_no_recommendations
            if isinstance(item, dict)
        ]
        if isinstance(raw_no_recommendations, list)
        else []
    )
    measurement_rows = list(
        await db.scalars(
            select(MeasurementWindow)
            .where(
                MeasurementWindow.workspace_id == workspace_id,
                MeasurementWindow.product_id == product_id,
            )
            .order_by(MeasurementWindow.created_at.desc())
            .limit(5)
        )
    )
    return TodayProjection(
        product_id=product_id,
        readiness=readiness,
        manual_run_state=(
            "active"
            if readiness.active_run_id
            else "available"
            if readiness.manual_run_available
            else "blocked"
        ),
        manual_run_reason=readiness.blockers[0] if readiness.blockers else None,
        plan=projected,
        run=_run(run) if run else None,
        latest_finalized_report=projected,
        current_run=_run(run) if run else None,
        source_health=SourceHealthProjection(
            github=(
                SourceHealthStatus.healthy
                if activation_state.github_installed and activation_state.monitored_repository_count
                else SourceHealthStatus.not_connected
            ),
            website=(
                SourceHealthStatus.healthy
                if activation_state.website_ready
                else SourceHealthStatus.not_connected
                if activation_state.website_source is None
                else SourceHealthStatus.stale
            ),
            search_console=(
                SourceHealthStatus.healthy
                if activation_state.search_connected
                else SourceHealthStatus.reduced
                if readiness.search_reduced_mode
                else SourceHealthStatus.not_connected
            ),
            observed_at=max(
                (
                    value
                    for value in (
                        activation_state.website_source.last_successful_crawl_at
                        if activation_state.website_source
                        else None,
                        activation_state.search_source.selected_at
                        if activation_state.search_source
                        else None,
                    )
                    if value is not None
                ),
                default=None,
            ),
        ),
        next_schedule=projected_next_schedule(product),
        approval_counts=ApprovalCounts(
            awaiting_approval=counts.get("pending", 0),
            approved=counts.get("approved", 0),
            rejected=counts.get("rejected", 0),
        ),
        shipped_summary=SummaryProjection(
            status=(
                "available"
                if activation_state.monitored_repository_count
                and _summary_items(plan_snapshot.get("shippedSummary"))
                else "no_changes"
                if activation_state.monitored_repository_count
                else "unavailable"
            ),
            reason=(
                None if activation_state.monitored_repository_count else "source_not_configured"
            ),
            items=_summary_items(plan_snapshot.get("shippedSummary")),
        ),
        search_summary=SummaryProjection(
            status=(
                "available"
                if _summary_items(plan_snapshot.get("searchSummary"))
                else "no_changes"
                if activation_state.search_connected
                else "unavailable"
            ),
            reason=None if activation_state.search_connected else "source_not_configured",
            items=_summary_items(plan_snapshot.get("searchSummary")),
        ),
        no_recommendation_outcomes=no_recommendations,
        recent_completed=[_action(item) for item in completed],
        recent_measurements=[_measurement_projection(item) for item in measurement_rows],
        source_recovery=readiness.source_recovery,
    )


@router.patch("/config", response_model=OperatorReadinessProjection)
async def patch_operator_config(
    workspace_id: UUID,
    product_id: UUID,
    payload: OperatorConfigPatch,
    request: Request,
    user: MutationUserDep,
    db: DbDep,
) -> OperatorReadinessProjection:
    product = await _access(db, workspace_id, product_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    fields = {
        "enabled": "weekly_growth_operator_enabled",
        "manual_enabled": "operator_manual_enabled",
        "scheduled_enabled": "operator_scheduled_enabled",
        "email_enabled": "operator_email_enabled",
        "timezone": "operator_timezone",
        "weekday": "operator_weekday",
        "hour": "operator_hour",
    }
    updates: dict[str, object] = {}
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if value is not None:
            updates[fields[field]] = value
    await update_operator_config(db, product, updates)
    return await get_operator_readiness(workspace_id, product_id, request, user, db)


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


@router.get("/plan-index", response_model=PlanListResponse)
async def list_plan_index(
    workspace_id: UUID,
    product_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PlanListResponse:
    await _access(db, workspace_id, product_id, user.id)
    query = select(MarketingPlan).where(
        MarketingPlan.workspace_id == workspace_id,
        MarketingPlan.product_id == product_id,
        MarketingPlan.status == "ready",
    )
    total = int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(
        await db.scalars(
            query.order_by(MarketingPlan.finalized_at.desc()).offset(offset).limit(limit)
        )
    )
    return PlanListResponse(
        items=[await _plan(db, row) for row in rows], page=_page(limit, offset, total)
    )


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


async def _recommendation(
    db: DbDep, row: OperatorRecommendation, actor_id: UUID | None = None
) -> RecommendationProjection:
    output = dict(row.output)
    decisions = list(
        await db.scalars(
            select(OperatorRecommendationDecision)
            .where(
                OperatorRecommendationDecision.workspace_id == row.workspace_id,
                OperatorRecommendationDecision.product_id == row.product_id,
                OperatorRecommendationDecision.recommendation_id == row.id,
            )
            .order_by(OperatorRecommendationDecision.created_at, OperatorRecommendationDecision.id)
        )
    )
    action = await db.scalar(
        select(PlanAction).where(
            PlanAction.workspace_id == row.workspace_id,
            PlanAction.product_id == row.product_id,
            PlanAction.recommendation_id == row.id,
        )
    )
    latest = decisions[-1].decision if decisions else None
    state = "dismissed" if action is not None and action.status == "dismissed" else latest
    feedback = (
        await get_actor_feedback(
            db,
            workspace_id=row.workspace_id,
            product_id=row.product_id,
            recommendation_id=row.id,
            actor_id=actor_id,
        )
        if actor_id
        else None
    )
    if state not in {"accepted", "dismissed"}:
        state = "proposed"
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
        state=state,
        valid_commands=[] if state != "proposed" else ["accept", "dismiss"],
        decision_history=[
            RecommendationDecisionProjection(
                id=item.id,
                decision=item.decision,
                reason=item.reason,
                comment=item.comment,
                actor_id=item.actor_id,
                created_at=item.created_at,
            )
            for item in decisions
        ],
        current_actor_feedback=RecommendationUsefulnessFeedbackProjection.model_validate(feedback)
        if feedback
        else None,
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
    return await _recommendation(
        db, await _get_recommendation(db, workspace_id, product_id, recommendation_id), user.id
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
            actor_id=user.id,
            reason=None,
            comment=None,
            idempotency_key=idempotency_key,
            fingerprint=command_fingerprint({}),
        )
    except CommandIdempotencyConflictError as exc:
        raise ConflictError(str(exc), code="idempotency_payload_conflict") from exc
    except DomainConflictError as exc:
        raise ConflictError(str(exc), code="invalid_recommendation_transition") from exc
    except LookupError as exc:
        raise NotFoundError("Recommendation not found.", code="recommendation_not_found") from exc
    return await _recommendation(db, row, user.id)


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
            actor_id=user.id,
            reason=payload.reason.value,
            comment=payload.comment,
            idempotency_key=idempotency_key,
            fingerprint=command_fingerprint(payload.model_dump(mode="json")),
        )
    except CommandIdempotencyConflictError as exc:
        raise ConflictError(str(exc), code="idempotency_payload_conflict") from exc
    except DomainConflictError as exc:
        raise ConflictError(str(exc), code="invalid_recommendation_transition") from exc
    except LookupError as exc:
        raise NotFoundError("Recommendation not found.", code="recommendation_not_found") from exc
    return await _recommendation(db, row, user.id)


@router.put(
    "/recommendations/{recommendation_id}/feedback",
    response_model=RecommendationUsefulnessFeedbackProjection,
)
async def put_recommendation_feedback(
    workspace_id: UUID,
    product_id: UUID,
    recommendation_id: UUID,
    payload: RecommendationUsefulnessFeedbackCommand,
    user: MutationUserDep,
    db: DbDep,
) -> RecommendationUsefulnessFeedbackProjection:
    await _access(db, workspace_id, product_id, user.id)
    await _get_recommendation(db, workspace_id, product_id, recommendation_id)
    row = await replace_recommendation_feedback(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        recommendation_id=recommendation_id,
        actor_id=user.id,
        rating=payload.rating.value,
        reason=payload.reason.value if payload.reason else None,
        comment=payload.comment,
    )
    return RecommendationUsefulnessFeedbackProjection.model_validate(row)


@router.put("/plans/{plan_id}/view", response_model=PlanViewProjection)
async def put_plan_view(
    workspace_id: UUID, product_id: UUID, plan_id: UUID, user: MutationUserDep, db: DbDep
) -> PlanViewProjection:
    await _access(db, workspace_id, product_id, user.id)
    plan = await _ready_plan(db, workspace_id, product_id, plan_id)
    try:
        row, _ = await record_plan_view(
            db, workspace_id=workspace_id, product_id=product_id, actor_id=user.id, plan=plan
        )
    except ValueError as exc:
        raise ConflictError(str(exc), code="plan_not_finalized") from exc
    return PlanViewProjection.model_validate(row)


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
            idempotency_key=idempotency_key,
            fingerprint=command_fingerprint(payload.model_dump(mode="json")),
        )
    except LookupError as exc:
        raise NotFoundError(
            "Action or exact asset revision not found.", code="action_not_found"
        ) from exc
    except DomainConflictError as exc:
        raise ConflictError(str(exc), code="stale_asset_revision") from exc
    except CommandIdempotencyConflictError as exc:
        raise ConflictError(str(exc), code="idempotency_payload_conflict") from exc
    return _action(action)


async def _transition(
    db: DbDep,
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    actor_id: UUID,
    target: str,
    reason: str | None,
    comment: str | None,
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
            comment=comment,
            idempotency_key=idempotency_key,
            fingerprint=command_fingerprint({"reason": reason, "comment": comment}),
        )
    except LookupError as exc:
        raise NotFoundError("Action not found.", code="action_not_found") from exc
    except DomainConflictError as exc:
        raise ConflictError(str(exc), code="invalid_action_transition") from exc
    except CommandIdempotencyConflictError as exc:
        raise ConflictError(str(exc), code="idempotency_payload_conflict") from exc


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
            payload.comment,
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
            db,
            workspace_id,
            product_id,
            action_id,
            user.id,
            "executing",
            None,
            None,
            idempotency_key,
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
            db,
            workspace_id,
            product_id,
            action_id,
            user.id,
            "completed",
            None,
            None,
            idempotency_key,
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
    latest = await db.scalar(
        select(PreparedAsset)
        .where(
            PreparedAsset.workspace_id == row.workspace_id,
            PreparedAsset.product_id == row.product_id,
            PreparedAsset.recommendation_id == row.recommendation_id,
            PreparedAsset.action_id == row.action_id,
        )
        .order_by(PreparedAsset.revision.desc())
        .limit(1)
    )
    action = (
        await db.scalar(select(PlanAction).where(PlanAction.id == row.action_id))
        if row.action_id
        else None
    )
    evidence = list(action.evidence_ids or []) if action else []
    current = latest or row
    legacy_draft_id = await db.scalar(
        select(drafts.c.id).where(
            drafts.c.workspace_id == row.workspace_id,
            drafts.c.prepared_asset_id == row.id,
            drafts.c.prepared_asset_revision == row.revision,
        )
    )
    bridge = None
    if row.kind == AssetKind.x_draft:
        bridge = XAssetBridgeProjection(
            state="linked" if legacy_draft_id else "unavailable",
            legacy_draft_id=legacy_draft_id,
            asset_revision=row.revision,
            reason=None if legacy_draft_id else "legacy_draft_not_created",
        )
    valid_commands: list[str] = []
    if current.id == row.id:
        valid_commands.append("edit")
        if approval is None or approval.status not in {"approved", "rejected"}:
            valid_commands.extend(["approve", "reject"])
    if legacy_draft_id:
        valid_commands.append("publish")
    return AssetProjection(
        id=row.id,
        product_id=row.product_id,
        recommendation_id=row.recommendation_id,
        action_id=row.action_id,
        revision=row.revision,
        kind=row.kind,
        structured_content=dict(row.structured_content),
        risk_outcome=review.final_outcome if review else None,
        approval_status=approval.status if approval else None,
        approval_required=action is not None and action.approval_level != "safe",
        is_current=current.id == row.id,
        current_asset_id=current.id,
        current_revision=current.revision,
        superseding_asset_id=current.id if current.id != row.id else None,
        evidence_references=evidence,
        created_at=row.created_at,
        rejected_reason=approval.reason if approval and approval.status == "rejected" else None,
        approved_at=approval.decided_at if approval and approval.status == "approved" else None,
        rejected_at=approval.decided_at if approval and approval.status == "rejected" else None,
        valid_commands=valid_commands,
        x_bridge=bridge,
    )


def _page(limit: int, offset: int, total: int) -> PageInfo:
    return PageInfo(limit=limit, offset=offset, total=total, has_next=offset + limit < total)


def _measurement_projection(row: MeasurementWindow) -> MeasurementProjection:
    return MeasurementProjection(
        id=row.id,
        action_id=row.action_id,
        product_id=row.product_id,
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


@router.get("/assets", response_model=AssetListResponse)
async def list_assets(
    workspace_id: UUID,
    product_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
    kind: AssetKind | None = None,
    approval_status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AssetListResponse:
    await _access(db, workspace_id, product_id, user.id)
    query = select(PreparedAsset).where(
        PreparedAsset.workspace_id == workspace_id, PreparedAsset.product_id == product_id
    )
    if kind is not None:
        query = query.where(PreparedAsset.kind == kind.value)
    if approval_status is not None:
        query = query.join(ApprovalRequest).where(ApprovalRequest.status == approval_status)
    total = int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(
        await db.scalars(
            query.order_by(PreparedAsset.created_at.desc()).offset(offset).limit(limit)
        )
    )
    return AssetListResponse(
        items=[await _asset_projection(db, row) for row in rows], page=_page(limit, offset, total)
    )


@router.get("/action-index", response_model=ActionListResponse)
async def list_action_index(
    workspace_id: UUID,
    product_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
    action_status: ActionStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ActionListResponse:
    await _access(db, workspace_id, product_id, user.id)
    query = select(PlanAction).where(
        PlanAction.workspace_id == workspace_id, PlanAction.product_id == product_id
    )
    if action_status is not None:
        query = query.where(PlanAction.status == action_status.value)
    total = int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(
        await db.scalars(query.order_by(PlanAction.created_at.desc()).offset(offset).limit(limit))
    )
    return ActionListResponse(
        items=[_action(row) for row in rows], page=_page(limit, offset, total)
    )


@router.get("/measurement-index", response_model=MeasurementListResponse)
async def list_measurements(
    workspace_id: UUID,
    product_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
    measurement_status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MeasurementListResponse:
    await _access(db, workspace_id, product_id, user.id)
    query = select(MeasurementWindow).where(
        MeasurementWindow.workspace_id == workspace_id, MeasurementWindow.product_id == product_id
    )
    if measurement_status is not None:
        query = query.where(MeasurementWindow.status == measurement_status)
    total = int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(
        await db.scalars(
            query.order_by(MeasurementWindow.created_at.desc()).offset(offset).limit(limit)
        )
    )
    return MeasurementListResponse(
        items=[_measurement_projection(row) for row in rows], page=_page(limit, offset, total)
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
        content = payload.structured_content.model_dump(mode="json")
        if "kind" not in content:
            content = {"kind": "x_draft", **content}
        new_asset = await revise_asset_use_case(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            asset_id=asset_id,
            revision=payload.effective_revision,
            structured_content=content,
            idempotency_key=idempotency_key,
            fingerprint=command_fingerprint(payload.model_dump(mode="json")),
        )
    except CommandIdempotencyConflictError as exc:
        raise ConflictError(str(exc), code="idempotency_payload_conflict") from exc
    except DomainConflictError as exc:
        root = await _get_asset(db, workspace_id, product_id, asset_id)
        current = await db.scalar(
            select(PreparedAsset)
            .where(
                PreparedAsset.workspace_id == workspace_id,
                PreparedAsset.product_id == product_id,
                PreparedAsset.recommendation_id == root.recommendation_id,
                PreparedAsset.action_id == root.action_id,
            )
            .order_by(PreparedAsset.revision.desc())
            .limit(1)
        )
        raise ConflictError(
            "Asset revision is stale.",
            code="stale_asset_revision",
            metadata={
                "resourceType": "prepared_asset",
                "requestedRevision": payload.effective_revision,
                "currentRevision": current.revision if current else root.revision,
                "currentAssetId": str(current.id if current else root.id),
                "currentResourceId": str(current.id if current else root.id),
                "revisionsHref": (
                    f"/workspaces/{workspace_id}/products/{product_id}/operator/"
                    f"assets/{asset_id}/revisions"
                ),
                "safeRecovery": "compare_or_reload",
            },
        ) from exc
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
        raise ConflictError(
            "Asset revision is stale.",
            code="stale_asset_revision",
            metadata={
                "resourceType": "prepared_asset",
                "requestedRevision": payload.revision,
                "currentRevision": row.revision,
                "currentResourceId": str(row.id),
                "revisionsHref": (
                    f"/workspaces/{workspace_id}/products/{product_id}/operator/"
                    f"assets/{asset_id}/revisions"
                ),
                "safeRecovery": "compare_or_reload",
            },
        )
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
            idempotency_key=idempotency_key,
            fingerprint=command_fingerprint(payload.model_dump(mode="json")),
            operation="asset.approve",
            command_resource_id=asset_id,
        )
    except LookupError as exc:
        raise NotFoundError(
            "Action or exact asset revision not found.", code="action_not_found"
        ) from exc
    except DomainConflictError as exc:
        raise ConflictError(str(exc), code="stale_asset_revision") from exc
    except CommandIdempotencyConflictError as exc:
        raise ConflictError(str(exc), code="idempotency_payload_conflict") from exc
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
    current_asset = await _get_asset(db, workspace_id, product_id, asset_id)
    if current_asset.revision != payload.revision:
        raise ConflictError(
            "Asset revision is stale.",
            code="stale_asset_revision",
            metadata={
                "resourceType": "prepared_asset",
                "requestedRevision": payload.revision,
                "currentRevision": current_asset.revision,
                "currentResourceId": str(current_asset.id),
                "revisionsHref": (
                    f"/workspaces/{workspace_id}/products/{product_id}/operator/"
                    f"assets/{asset_id}/revisions"
                ),
                "safeRecovery": "compare_or_reload",
            },
        )
    try:
        row = await reject_asset_use_case(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            asset_id=asset_id,
            revision=payload.revision,
            actor_id=user.id,
            reason=payload.reason,
            idempotency_key=idempotency_key,
            fingerprint=command_fingerprint(payload.model_dump(mode="json")),
        )
    except CommandIdempotencyConflictError as exc:
        raise ConflictError(str(exc), code="idempotency_payload_conflict") from exc
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
