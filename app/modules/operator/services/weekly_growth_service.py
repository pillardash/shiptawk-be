import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.outbox import OutboxMetadata, enqueue_outbox_event
from app.modules.operator.models import (
    ActionEvent,
    ActionRiskReview,
    ApprovalRequest,
    MarketingPlan,
    MeasurementWindow,
    OperatorRecommendation,
    OperatorRun,
    Opportunity,
    OpportunityEvaluation,
    PlanAction,
    PreparedAsset,
)
from app.modules.operator.policies import WRITE_ROLES, require_role
from app.modules.operator.policies.weekly_growth_policy import (
    DomainConflictError,
    assert_exact_asset_revision,
    build_plan_snapshot,
    scheduled_weekly_key,
    transition_action,
    transition_plan,
)
from app.modules.operator.repositories import weekly_growth_repository as repository
from app.modules.operator.schemas.ai_output_schema import PreparedAssetOutput


def _object_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _object_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _object_int(value: object, default: int) -> int:
    return int(value) if isinstance(value, (int, str)) else default


async def select_current_run_opportunities(
    db: AsyncSession,
    *,
    run: OperatorRun,
    detection_run_id: UUID,
    limit: int = 3,
) -> list[Opportunity]:
    """Persist Phase 4 priority selection once; retries replay the exact IDs."""
    if run.product_id is None or run.run_kind != "weekly_growth":
        raise DomainConflictError("Selection requires a product-scoped weekly run.")
    if not 0 <= limit <= 3:
        raise ValueError("Weekly selection limit must be between zero and three.")
    summary = dict(run.stage_summary or {})
    persisted = summary.get("selectedOpportunityIds")
    if isinstance(persisted, list):
        ids = [UUID(str(value)) for value in persisted]
        if not ids:
            return []
        rows = list(
            await db.scalars(
                select(Opportunity).where(
                    Opportunity.workspace_id == run.workspace_id,
                    Opportunity.product_id == run.product_id,
                    Opportunity.id.in_(ids),
                )
            )
        )
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(ids):
            raise DomainConflictError("Persisted weekly selection is incomplete.")
        return [by_id[item_id] for item_id in ids]
    selected = list(
        await db.scalars(
            select(Opportunity)
            .join(
                OpportunityEvaluation,
                OpportunityEvaluation.accepted_opportunity_id == Opportunity.id,
            )
            .where(
                Opportunity.workspace_id == run.workspace_id,
                Opportunity.product_id == run.product_id,
                Opportunity.status == "open",
                OpportunityEvaluation.workspace_id == run.workspace_id,
                OpportunityEvaluation.product_id == run.product_id,
                OpportunityEvaluation.operator_run_id == detection_run_id,
                OpportunityEvaluation.outcome == "accepted",
            )
            .order_by(
                OpportunityEvaluation.priority_score.desc().nullslast(),
                OpportunityEvaluation.id,
            )
            .limit(limit)
        )
    )
    summary["detectionRunId"] = str(detection_run_id)
    summary["selectedOpportunityIds"] = [str(item.id) for item in selected]
    run.stage_summary = summary
    run.current_stage = "selected"
    await db.flush()
    return selected


async def create_weekly_run(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    period_start: datetime,
    period_end: datetime,
    workflow_version: str,
    trigger: str,
    idempotency_key: str | None = None,
) -> OperatorRun:
    if period_end <= period_start:
        raise ValueError("period_end must be after period_start.")
    key = (
        scheduled_weekly_key(product_id, period_start, workflow_version)
        if trigger == "scheduled"
        else (idempotency_key or "").strip()
    )
    if not key:
        raise ValueError("Manual runs require a caller-supplied idempotency key.")
    replay = await repository.get_run_by_key(db, workspace_id, product_id, key)
    if replay is not None:
        return replay
    run = OperatorRun(
        workspace_id=workspace_id,
        product_id=product_id,
        run_kind="weekly_growth",
        trigger=trigger,
        status="pending",
        idempotency_key=key,
        logical_period_start=period_start,
        logical_period_end=period_end,
        workflow_version=workflow_version,
        current_stage="created",
        stage_summary={},
        analyzer_version=workflow_version,
        provenance={},
        summary={},
        created_by_actor_id=actor_id,
    )
    await repository.flush(db, run)
    return run


async def request_manual_weekly_run(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    period_start: datetime,
    period_end: datetime,
    workflow_version: str,
    idempotency_key: str,
) -> OperatorRun:
    await require_role(db, workspace_id, actor_id, WRITE_ROLES)
    run = await create_weekly_run(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=actor_id,
        period_start=period_start,
        period_end=period_end,
        workflow_version=workflow_version,
        trigger="manual",
        idempotency_key=idempotency_key,
    )
    await enqueue_outbox_event(
        db,
        event_name="operator/weekly-growth.requested",
        schema_version=1,
        idempotency_key=str(run.id),
        aggregate_id=run.id,
        workspace_id=workspace_id,
        product_id=product_id,
        metadata=OutboxMetadata(
            {
                "runId": str(run.id),
                "workspaceId": str(workspace_id),
                "productId": str(product_id),
                "schemaVersion": 1,
                "correlationId": str(run.id),
            }
        ),
    )
    await db.commit()
    return run


async def create_canonical_plan(
    db: AsyncSession,
    *,
    run: OperatorRun,
    actor_id: UUID,
    recommendations: Sequence[OperatorRecommendation],
    selection_policy_version: str,
) -> MarketingPlan:
    if run.product_id is None or run.run_kind != "weekly_growth":
        raise DomainConflictError("Canonical plans require a product-scoped weekly run.")
    if len(recommendations) > 3:
        raise ValueError("Canonical weekly plans contain at most three recommendations.")
    for recommendation in recommendations:
        if (
            recommendation.product_id != run.product_id
            or recommendation.workspace_id != run.workspace_id
        ):
            raise DomainConflictError("Recommendation belongs to another workspace or product.")
    period_start = run.logical_period_start
    period_end = run.logical_period_end
    if period_start is None or period_end is None:
        raise DomainConflictError("Weekly run has no logical period.")
    plan = MarketingPlan(
        workspace_id=run.workspace_id,
        product_id=run.product_id,
        operator_run_id=run.id,
        title=f"Weekly growth plan: {period_start.date().isoformat()}",
        status="preparing",
        period_start=period_start,
        period_end=period_end,
        idempotency_key=f"{run.idempotency_key}:plan:1",
        provenance={"runId": str(run.id)},
        plan_revision=1,
        schema_version=1,
        selection_policy_version=selection_policy_version,
        action_count=len(recommendations),
        no_recommendation_count=0,
        created_by_actor_id=actor_id,
    )
    await repository.flush(db, plan)
    for position, recommendation in enumerate(recommendations, start=1):
        output = recommendation.output
        db.add(
            PlanAction(
                workspace_id=run.workspace_id,
                product_id=run.product_id,
                plan_id=plan.id,
                opportunity_id=recommendation.opportunity_id,
                recommendation_id=recommendation.id,
                position=position,
                title=str(output.get("title", "Growth recommendation")),
                description=str(output.get("recommendation", output.get("summary", ""))),
                evidence_ids=_object_list(output.get("evidenceIds", [])),
                confidence=str(output.get("confidence", "medium")),
                missing_information=_object_list(output.get("missingInformation", [])),
                approval_level=str(output.get("approvalLevel", "review")),
                approval_status="pending",
                status="pending",
                cooldown_dedup_key=f"recommendation:{recommendation.id}",
                idempotency_key=f"{plan.idempotency_key}:action:{recommendation.id}",
                provenance={"recommendationId": str(recommendation.id)},
                prepared_output_type=str(output.get("preparedOutputType", "no_asset")),
                expected_metric=_object_dict(output.get("expectedMetric", {})),
                measurement_window_days=_object_int(output.get("measurementWindowDays", 28), 28),
                estimated_effort=str(output.get("estimatedEffort", "medium")),
            )
        )
        recommendation.accepted_at = datetime.now(UTC)
    await db.flush()
    return plan


async def finalize_plan(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    plan_id: UUID,
    no_recommendations: Sequence[Mapping[str, object]],
    shipped_summary: Mapping[str, object],
    search_summary: Mapping[str, object],
    now: datetime | None = None,
) -> MarketingPlan:
    plan = await repository.get_plan(db, workspace_id, product_id, plan_id, lock=True)
    if plan is None:
        raise LookupError("Plan not found.")
    if plan.status == "ready":
        return plan
    transition_plan(plan.status, "ready")
    actions = await repository.list_plan_actions(db, workspace_id, product_id, plan_id)
    if plan.operator_run_id is None:
        raise DomainConflictError("Canonical plan has no operator run.")
    snapshot = build_plan_snapshot(
        run_id=plan.operator_run_id,
        plan_id=plan.id,
        revision=plan.plan_revision or 1,
        actions=[
            {
                "id": action.id,
                "position": action.position,
                "recommendationId": action.recommendation_id,
            }
            for action in actions
        ],
        no_recommendations=no_recommendations,
        shipped_summary=shipped_summary,
        search_summary=search_summary,
    )
    plan.plan_snapshot = dict(snapshot)
    plan.no_recommendation_count = len(no_recommendations)
    plan.action_count = len(actions)
    plan.status = "ready"
    plan.finalized_at = now or datetime.now(UTC)
    await db.flush()
    return plan


async def decide_recommendation(
    db: AsyncSession, *, workspace_id: UUID, product_id: UUID, recommendation_id: UUID, accept: bool
) -> OperatorRecommendation:
    recommendation = await repository.get_recommendation(
        db, workspace_id, product_id, recommendation_id
    )
    if recommendation is None:
        raise LookupError("Recommendation not found.")
    recommendation.accepted_at = recommendation.accepted_at or datetime.now(UTC) if accept else None
    await db.flush()
    return recommendation


async def decide_recommendation_use_case(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    recommendation_id: UUID,
    accept: bool,
) -> OperatorRecommendation:
    try:
        recommendation = await decide_recommendation(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            recommendation_id=recommendation_id,
            accept=accept,
        )
        await db.commit()
        return recommendation
    except Exception:
        await db.rollback()
        raise


async def approve_exact_revision(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    asset_id: UUID,
    revision: int,
    actor_id: UUID,
) -> ApprovalRequest:
    action = await repository.get_action(db, workspace_id, product_id, action_id, lock=True)
    asset = await repository.get_exact_asset(db, workspace_id, product_id, asset_id, revision)
    approval = await repository.get_pending_approval(db, workspace_id, product_id, action_id)
    if action is None or asset is None or approval is None or asset.action_id != action.id:
        raise LookupError("Action, asset revision, or pending approval not found.")
    approval.asset_id, approval.asset_revision = asset_id, revision
    approval.status, approval.decided_by_actor_id = "approved", actor_id
    approval.decided_at = datetime.now(UTC)
    action.approved_asset_id, action.approved_asset_revision = asset_id, revision
    action.approval_status = "approved"
    action.status = transition_action(action.status, "ready")
    await db.flush()
    from app.modules.operator.services.x_asset_bridge_service import project_x_asset_to_draft

    await project_x_asset_to_draft(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        asset_id=asset_id,
        revision=revision,
    )
    return approval


async def approve_action_revision_use_case(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    asset_id: UUID,
    revision: int,
    actor_id: UUID,
) -> PlanAction:
    try:
        action = await repository.get_action(db, workspace_id, product_id, action_id, lock=True)
        if action is None:
            raise LookupError("Action or exact asset revision not found.")
        if (
            action.approval_status == "approved"
            and action.approved_asset_id == asset_id
            and action.approved_asset_revision == revision
        ):
            return action
        asset = await repository.get_exact_asset(db, workspace_id, product_id, asset_id, revision)
        if asset is None or asset.action_id != action_id:
            raise LookupError("Action or exact asset revision not found.")
        latest_revision = await db.scalar(
            select(func.max(PreparedAsset.revision)).where(
                PreparedAsset.workspace_id == workspace_id,
                PreparedAsset.product_id == product_id,
                PreparedAsset.recommendation_id == asset.recommendation_id,
                PreparedAsset.action_id == action_id,
            )
        )
        if latest_revision != revision:
            raise DomainConflictError("Asset revision is stale.")
        review = await db.scalar(
            select(ActionRiskReview).where(
                ActionRiskReview.workspace_id == workspace_id,
                ActionRiskReview.product_id == product_id,
                ActionRiskReview.asset_id == asset_id,
                ActionRiskReview.asset_revision == revision,
            )
        )
        if review is None or review.final_outcome == "block":
            raise DomainConflictError("Asset risk is not current or is blocked.")
        await approve_exact_revision(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            action_id=action_id,
            asset_id=asset_id,
            revision=revision,
            actor_id=actor_id,
        )
        await db.commit()
        return action
    except Exception:
        await db.rollback()
        raise


async def transition_canonical_action(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    target: str,
    actor_id: UUID,
    reason: str | None = None,
    now: datetime | None = None,
) -> PlanAction:
    action = await repository.get_action(db, workspace_id, product_id, action_id, lock=True)
    if action is None:
        raise LookupError("Action not found.")
    timestamp = now or datetime.now(UTC)
    if target == "executing" and action.approved_asset_id is not None:
        asset = await repository.get_exact_asset(
            db,
            workspace_id,
            product_id,
            action.approved_asset_id,
            action.approved_asset_revision or 0,
        )
        if asset is None:
            raise DomainConflictError("Approved asset revision no longer exists.")
        assert_exact_asset_revision(
            action.approved_asset_id, action.approved_asset_revision, asset.id, asset.revision
        )
    previous = action.status
    action.status = transition_action(previous, target)
    if target == "completed":
        action.completed_by_actor_id, action.completed_at = actor_id, timestamp
    if target == "dismissed":
        action.dismissed_by_actor_id, action.dismissed_at = actor_id, timestamp
        action.dismissal_reason = reason
    event = ActionEvent(
        workspace_id=workspace_id,
        product_id=product_id,
        action_id=action.id,
        event_type=target,
        previous_status=previous,
        new_status=target,
        actor_id=actor_id,
        reason=reason,
        details={},
    )
    await repository.flush(db, event)
    return action


async def transition_action_use_case(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    target: str,
    actor_id: UUID,
    idempotency_key: str,
    reason: str | None = None,
) -> PlanAction:
    try:
        action = await repository.get_action(db, workspace_id, product_id, action_id, lock=True)
        if action is None:
            raise LookupError("Action not found.")
        if action.status == target:
            return action
        action = await transition_canonical_action(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            action_id=action_id,
            target=target,
            actor_id=actor_id,
            reason=reason,
        )
        if target == "completed":
            now = action.completed_at or datetime.now(UTC)
            measurement = await schedule_measurement(
                db,
                workspace_id=workspace_id,
                product_id=product_id,
                action_id=action_id,
                idempotency_key=f"{idempotency_key}:measurement",
                starts_at=now,
            )
            await enqueue_outbox_event(
                db,
                event_name="operator/action-measurement.requested",
                schema_version=1,
                idempotency_key=idempotency_key,
                aggregate_id=action_id,
                workspace_id=workspace_id,
                product_id=product_id,
                metadata=OutboxMetadata(
                    {
                        "actionId": str(action_id),
                        "measurementId": str(measurement.id),
                        "workspaceId": str(workspace_id),
                        "productId": str(product_id),
                        "schemaVersion": 1,
                    }
                ),
            )
        await db.commit()
        return action
    except Exception:
        await db.rollback()
        raise


async def revise_asset_use_case(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    asset_id: UUID,
    revision: int,
    structured_content: dict[str, object],
) -> PreparedAsset:
    try:
        root = await repository.get_exact_asset(db, workspace_id, product_id, asset_id, revision)
        if root is None:
            root = await db.scalar(
                select(PreparedAsset).where(
                    PreparedAsset.workspace_id == workspace_id,
                    PreparedAsset.product_id == product_id,
                    PreparedAsset.id == asset_id,
                )
            )
        if root is None:
            raise LookupError("Asset not found.")
        latest = await db.scalar(
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
        candidate = {**structured_content, "kind": root.kind}
        try:
            validated: PreparedAssetOutput = TypeAdapter(PreparedAssetOutput).validate_python(
                candidate
            )
        except ValidationError as exc:
            raise ValueError("Structured content does not match the asset kind.") from exc
        content = validated.model_dump(by_alias=True)
        if latest is None:
            raise LookupError("Asset not found.")
        if revision != latest.revision:
            if revision + 1 == latest.revision and latest.structured_content == content:
                return latest
            raise DomainConflictError("Asset revision is stale.")
        new_asset = PreparedAsset(
            workspace_id=workspace_id,
            product_id=product_id,
            recommendation_id=root.recommendation_id,
            action_id=root.action_id,
            llm_execution_id=root.llm_execution_id,
            revision=latest.revision + 1,
            kind=root.kind,
            structured_content=content,
        )
        db.add(new_asset)
        if root.action_id is not None:
            action = await repository.get_action(
                db, workspace_id, product_id, root.action_id, lock=True
            )
            if action is None:
                raise LookupError("Action not found.")
            action.approved_asset_id = None
            action.approved_asset_revision = None
            action.approval_status = "pending"
            if action.status == "ready":
                action.status = "pending"
            approval = await db.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.workspace_id == workspace_id,
                    ApprovalRequest.product_id == product_id,
                    ApprovalRequest.action_id == action.id,
                )
            )
            if approval is not None:
                approval.status = "pending"
                approval.asset_id = None
                approval.asset_revision = None
                approval.decided_at = None
                approval.decided_by_actor_id = None
        await db.commit()
        await db.refresh(new_asset)
        return new_asset
    except Exception:
        await db.rollback()
        raise


async def reject_asset_use_case(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    asset_id: UUID,
    revision: int,
    actor_id: UUID,
    reason: str | None,
) -> PreparedAsset:
    try:
        asset = await repository.get_exact_asset(db, workspace_id, product_id, asset_id, revision)
        if asset is None:
            raise LookupError("Asset not found.")
        approval = await db.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.workspace_id == workspace_id,
                ApprovalRequest.product_id == product_id,
                ApprovalRequest.action_id == asset.action_id,
            )
        )
        if approval is None:
            raise LookupError("Approval request not found.")
        approval.status = "rejected"
        approval.asset_id, approval.asset_revision = asset.id, asset.revision
        approval.reason, approval.decided_by_actor_id = reason, actor_id
        approval.decided_at = datetime.now(UTC)
        if asset.action_id is not None:
            action = await repository.get_action(
                db, workspace_id, product_id, asset.action_id, lock=True
            )
            if action is None:
                raise LookupError("Action not found.")
            action.approval_status, action.status = "rejected", "pending"
        await db.commit()
        return asset
    except Exception:
        await db.rollback()
        raise


async def schedule_measurement(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    action_id: UUID,
    idempotency_key: str,
    starts_at: datetime,
) -> MeasurementWindow:
    action = await repository.get_action(db, workspace_id, product_id, action_id)
    if action is None:
        raise LookupError("Action not found.")
    days = action.measurement_window_days or 28
    due_at = starts_at + timedelta(days=days)
    action.measurement_due_at = due_at
    existing = await db.scalar(
        select(MeasurementWindow).where(
            MeasurementWindow.workspace_id == workspace_id,
            MeasurementWindow.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing
    from app.modules.operator.services.measurement_service import read_search_measurement

    baseline = await read_search_measurement(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        contract=action.expected_metric or {},
        starts_on=(starts_at - timedelta(days=days)).date(),
        ends_on=starts_at.date(),
    )
    measurement = MeasurementWindow(
        workspace_id=workspace_id,
        product_id=product_id,
        action_id=action_id,
        status="scheduled",
        starts_at=starts_at,
        ends_at=due_at,
        due_at=due_at,
        metric_contract=action.expected_metric or {},
        expected_metric=action.expected_metric or {},
        baseline=baseline or {},
        baseline_period={
            "startsOn": (starts_at - timedelta(days=days)).date().isoformat(),
            "endsOn": starts_at.date().isoformat(),
        },
        baseline_value=baseline,
        idempotency_key=idempotency_key,
        provenance={"actionId": str(action_id)},
        limitations=[],
    )
    await repository.flush(db, measurement)
    await enqueue_outbox_event(
        db,
        event_name="operator/measurement.followup",
        schema_version=1,
        idempotency_key=str(measurement.id),
        aggregate_id=measurement.id,
        workspace_id=workspace_id,
        product_id=product_id,
        available_at=due_at,
        metadata=OutboxMetadata(
            {
                "measurementId": str(measurement.id),
                "workspaceId": str(workspace_id),
                "productId": str(product_id),
                "schemaVersion": 1,
                "correlationId": str(measurement.id),
            }
        ),
    )
    return measurement


def snapshot_hash(plan: MarketingPlan) -> str:
    if plan.status != "ready" or plan.plan_snapshot is None:
        raise DomainConflictError("Only finalized plans have a delivery hash.")
    encoded = json.dumps(plan.plan_snapshot, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
