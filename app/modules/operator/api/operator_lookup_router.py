from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, func, literal, select, union_all

from app.api.deps import DbDep, get_current_user
from app.modules.identity.models.users import User
from app.modules.operator.api.weekly_growth_router import (
    _action,
    _asset_projection,
    _recommendation,
)
from app.modules.operator.models import (
    ActionEvent,
    MarketingPlan,
    MeasurementWindow,
    OperatorRecommendation,
    OperatorRun,
    OperatorRunSnapshot,
    Opportunity,
    OpportunityEvidence,
    PlanAction,
    PreparedAsset,
)
from app.modules.operator.schemas.weekly_growth_api_schema import (
    ActionDetailResponse,
    AssetProjection,
    HistoryEntryProjection,
    HistoryListResponse,
    HistoryStatus,
    MeasurementProjection,
    RunDetailProjection,
    SafeEvidenceProjection,
)
from app.modules.products.models import Product
from app.modules.workspaces.services import require_active_workspace_role
from app.shared.exceptions import NotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}/operator", tags=["operator-history"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


async def _access(db: DbDep, workspace_id: UUID, user_id: UUID) -> None:
    await require_active_workspace_role(
        db,
        workspace_id,
        user_id,
        missing=NotFoundError("Operator resource not found.", code="operator_resource_not_found"),
    )


def _measurement(row: MeasurementWindow | None) -> MeasurementProjection | None:
    if row is None:
        return None
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


@router.get("/runs/{run_id}", response_model=RunDetailProjection)
async def get_run_by_id(
    workspace_id: UUID, run_id: UUID, user: CurrentUserDep, db: DbDep
) -> RunDetailProjection:
    await _access(db, workspace_id, user.id)
    run = await db.scalar(
        select(OperatorRun).where(
            OperatorRun.workspace_id == workspace_id, OperatorRun.id == run_id
        )
    )
    if run is None or run.product_id is None:
        raise NotFoundError("Operator run not found.", code="operator_run_not_found")
    plan = await db.scalar(
        select(MarketingPlan).where(
            MarketingPlan.workspace_id == workspace_id,
            MarketingPlan.product_id == run.product_id,
            MarketingPlan.operator_run_id == run.id,
        )
    )
    actions = (
        list(
            await db.scalars(
                select(PlanAction)
                .where(
                    PlanAction.workspace_id == workspace_id,
                    PlanAction.product_id == run.product_id,
                    PlanAction.plan_id == plan.id,
                )
                .order_by(PlanAction.position)
            )
        )
        if plan
        else []
    )
    snapshot = await db.scalar(
        select(OperatorRunSnapshot).where(
            OperatorRunSnapshot.workspace_id == workspace_id,
            OperatorRunSnapshot.operator_run_id == run.id,
        )
    )
    payload = dict(snapshot.input_payload) if snapshot else {}
    plan_snapshot = dict(plan.plan_snapshot or {}) if plan else {}
    no_recommendations = plan_snapshot.get("noRecommendations", [])
    return RunDetailProjection(
        id=run.id,
        product_id=run.product_id,
        plan_id=plan.id if plan else None,
        plan_revision=(plan.plan_revision or 1) if plan else None,
        run_kind=run.run_kind,
        trigger=run.trigger,
        status=run.status,
        current_stage=run.current_stage,
        stage_summary=dict(run.stage_summary or {}),
        failure_category=run.failure_category,
        period_start=run.logical_period_start,
        period_end=run.logical_period_end,
        started_at=run.started_at,
        completed_at=run.completed_at,
        profile_revision=payload.get("profileRevision")
        if isinstance(payload.get("profileRevision"), int)
        else None,
        source_freshness=_mapping(payload.get("sourceFreshness")),
        shipped_summary=_mapping(plan_snapshot.get("shippedSummary")),
        search_summary=_mapping(plan_snapshot.get("searchSummary")),
        actions=[_action(action) for action in actions],
        no_recommendation_outcomes=list(no_recommendations)
        if isinstance(no_recommendations, list)
        else [],
    )


@router.get("/actions/{action_id}", response_model=ActionDetailResponse)
async def get_action_by_id(
    workspace_id: UUID, action_id: UUID, user: CurrentUserDep, db: DbDep
) -> ActionDetailResponse:
    await _access(db, workspace_id, user.id)
    action = await db.scalar(
        select(PlanAction).where(
            PlanAction.workspace_id == workspace_id, PlanAction.id == action_id
        )
    )
    if action is None or action.product_id is None:
        raise NotFoundError("Action not found.", code="action_not_found")
    recommendation = (
        await db.scalar(
            select(OperatorRecommendation).where(
                OperatorRecommendation.workspace_id == workspace_id,
                OperatorRecommendation.product_id == action.product_id,
                OperatorRecommendation.id == action.recommendation_id,
            )
        )
        if action.recommendation_id
        else None
    )
    opportunity = (
        await db.scalar(
            select(Opportunity).where(
                Opportunity.workspace_id == workspace_id,
                Opportunity.product_id == action.product_id,
                Opportunity.id == action.opportunity_id,
            )
        )
        if action.opportunity_id
        else None
    )
    evidence_rows = list(
        await db.scalars(
            select(OpportunityEvidence).where(
                OpportunityEvidence.workspace_id == workspace_id,
                OpportunityEvidence.product_id == action.product_id,
                OpportunityEvidence.opportunity_id == action.opportunity_id,
            )
        )
    )
    asset = await db.scalar(
        select(PreparedAsset)
        .where(
            PreparedAsset.workspace_id == workspace_id,
            PreparedAsset.product_id == action.product_id,
            PreparedAsset.action_id == action.id,
        )
        .order_by(PreparedAsset.revision.desc())
        .limit(1)
    )
    events = list(
        await db.scalars(
            select(ActionEvent)
            .where(ActionEvent.workspace_id == workspace_id, ActionEvent.action_id == action.id)
            .order_by(ActionEvent.created_at)
        )
    )
    measurement = await db.scalar(
        select(MeasurementWindow)
        .where(
            MeasurementWindow.workspace_id == workspace_id, MeasurementWindow.action_id == action.id
        )
        .order_by(MeasurementWindow.created_at.desc())
        .limit(1)
    )
    return ActionDetailResponse(
        action=_action(action),
        recommendation=await _recommendation(db, recommendation, user.id)
        if recommendation
        else None,
        evidence=[
            SafeEvidenceProjection(
                id=item.id,
                source_type=item.source_type,
                source_record_type=item.source_record_type,
                period=_mapping(item.period),
                observed_at=item.observed_at,
                freshness_status=item.freshness_status,
                public_safe_summary=item.public_safe_summary,
                metrics=dict(item.metrics),
                comparison=_mapping(item.metrics.get("comparison")) or None,
                provenance={"evidenceId": str(item.id)},
            )
            for item in evidence_rows
        ],
        interpretation=opportunity.interpretation if opportunity else None,
        recommendation_text=opportunity.recommendation if opportunity else action.description,
        prepared_output=await _asset_projection(db, asset) if asset else None,
        expected_metric=dict(action.expected_metric or {}),
        measurement_window_days=action.measurement_window_days,
        confidence=action.confidence,
        estimated_effort=action.estimated_effort,
        missing_information=list(action.missing_information or []),
        approval_requirement=action.approval_level,
        approval_state=action.approval_status,
        action_history=[
            {
                "type": event.event_type,
                "from": event.previous_status,
                "to": event.new_status,
                "at": event.created_at.isoformat(),
            }
            for event in events
        ],
        measurement=_measurement(measurement),
    )


@router.get("/assets/{asset_id}", response_model=AssetProjection)
async def get_asset_by_id(
    workspace_id: UUID, asset_id: UUID, user: CurrentUserDep, db: DbDep
) -> AssetProjection:
    await _access(db, workspace_id, user.id)
    asset = await db.scalar(
        select(PreparedAsset).where(
            PreparedAsset.workspace_id == workspace_id, PreparedAsset.id == asset_id
        )
    )
    if asset is None:
        raise NotFoundError("Asset not found.", code="asset_not_found")
    return await _asset_projection(db, asset)


@router.get("/history", response_model=HistoryListResponse)
async def list_history(
    workspace_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
    product_id: UUID | None = None,
    entry_type: str | None = None,
    status: HistoryStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HistoryListResponse:
    await _access(db, workspace_id, user.id)
    run_query = select(
        OperatorRun.id.label("id"),
        OperatorRun.product_id.label("product_id"),
        literal("run", String).label("entry_type"),
        OperatorRun.status.label("status"),
        literal("Weekly operator run", String).label("title"),
        OperatorRun.started_at.label("occurred_at"),
        OperatorRun.id.label("resource_id"),
        literal(None).label("action_id"),
    ).where(OperatorRun.workspace_id == workspace_id, OperatorRun.product_id.is_not(None))
    action_query = select(
        PlanAction.id.label("id"),
        PlanAction.product_id.label("product_id"),
        literal("action", String).label("entry_type"),
        PlanAction.status.label("status"),
        PlanAction.title.label("title"),
        PlanAction.created_at.label("occurred_at"),
        PlanAction.id.label("resource_id"),
        PlanAction.id.label("action_id"),
    ).where(PlanAction.workspace_id == workspace_id, PlanAction.product_id.is_not(None))
    measurement_query = select(
        MeasurementWindow.id.label("id"),
        MeasurementWindow.product_id.label("product_id"),
        literal("measurement", String).label("entry_type"),
        MeasurementWindow.status.label("status"),
        literal("Measurement", String).label("title"),
        MeasurementWindow.created_at.label("occurred_at"),
        MeasurementWindow.id.label("resource_id"),
        MeasurementWindow.action_id.label("action_id"),
    ).where(
        MeasurementWindow.workspace_id == workspace_id, MeasurementWindow.product_id.is_not(None)
    )
    if entry_type == "run":
        combined = union_all(run_query).subquery()
    elif entry_type == "action":
        combined = union_all(action_query).subquery()
    elif entry_type == "measurement":
        combined = union_all(measurement_query).subquery()
    else:
        combined = union_all(run_query, action_query, measurement_query).subquery()
    query = (
        select(combined, Product.name)
        .join(Product, Product.id == combined.c.product_id)
        .where(Product.workspace_id == workspace_id)
    )
    if product_id is not None:
        query = query.where(combined.c.product_id == product_id)
    if status is not None:
        query = query.where(combined.c.status == status.value)
    total = int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = (
        await db.execute(query.order_by(combined.c.occurred_at.desc()).offset(offset).limit(limit))
    ).all()
    return HistoryListResponse(
        items=[
            HistoryEntryProjection(
                id=row.id,
                product_id=row.product_id,
                product_name=row.name,
                entry_type=row.entry_type,
                status=row.status,
                title=row.title,
                occurred_at=row.occurred_at,
                resource_id=row.resource_id,
                action_id=row.action_id,
                canonical_destination=(
                    f"/users/history/runs/{row.resource_id}"
                    if row.entry_type == "run"
                    else f"/users/history/actions/{row.action_id}"
                ),
            )
            for row in rows
        ],
        page={"limit": limit, "offset": offset, "total": total, "hasNext": offset + limit < total},
    )
