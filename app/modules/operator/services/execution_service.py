from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import (
    ActionRiskReview,
    ApprovalRequest,
    ExecutionRun,
    MeasurementWindow,
    PlanAction,
    PreparedAsset,
    VerificationRun,
)
from app.modules.operator.policies import WRITE_ROLES, action_has_required_approval, require_role
from app.shared.exceptions import ConflictError, NotFoundError


async def execute_action(
    db: AsyncSession,
    workspace_id: UUID,
    plan_id: UUID,
    action_id: UUID,
    actor_id: UUID,
    idempotency_key: str,
) -> ExecutionRun:
    await require_role(db, workspace_id, actor_id, WRITE_ROLES)
    replay = await db.scalar(
        select(ExecutionRun).where(
            ExecutionRun.workspace_id == workspace_id,
            ExecutionRun.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        return replay
    action = await db.scalar(
        select(PlanAction).where(
            PlanAction.workspace_id == workspace_id,
            PlanAction.plan_id == plan_id,
            PlanAction.id == action_id,
        )
    )
    if action is None:
        raise NotFoundError("Plan action not found.", code="plan_action_not_found")
    existing_execution = await db.scalar(
        select(ExecutionRun).where(
            ExecutionRun.workspace_id == workspace_id,
            ExecutionRun.action_id == action_id,
            ExecutionRun.status != "failed",
        )
    )
    if existing_execution is not None:
        raise ConflictError(
            "This action has already been executed.", code="action_already_executed"
        )
    approval = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.workspace_id == workspace_id,
            ApprovalRequest.action_id == action_id,
        )
    )
    asset = None
    review = None
    if approval is not None and approval.asset_id is not None and approval.product_id is not None:
        asset = await db.scalar(
            select(PreparedAsset).where(
                PreparedAsset.workspace_id == workspace_id,
                PreparedAsset.product_id == approval.product_id,
                PreparedAsset.id == approval.asset_id,
                PreparedAsset.revision == approval.asset_revision,
            )
        )
        if asset is not None:
            review = await db.scalar(
                select(ActionRiskReview).where(
                    ActionRiskReview.workspace_id == workspace_id,
                    ActionRiskReview.product_id == approval.product_id,
                    ActionRiskReview.asset_id == asset.id,
                    ActionRiskReview.asset_revision == asset.revision,
                )
            )
    if not action_has_required_approval(action, approval, asset, review):
        raise ConflictError(
            "This action requires approval before execution.", code="action_approval_required"
        )
    if action.status != "ready":
        raise ConflictError("This action is not ready for execution.", code="action_not_ready")
    now = datetime.now(UTC)
    execution = ExecutionRun(
        workspace_id=workspace_id,
        action_id=action_id,
        approval_request_id=approval.id if approval else None,
        status="completed",
        idempotency_key=idempotency_key,
        output={"result": "prepared", "sideEffects": []},
        provenance={"executor": "deterministic-local", "version": "1"},
        started_at=now,
        completed_at=now,
        created_by_actor_id=actor_id,
    )
    db.add(execution)
    action.status = "completed"
    await db.flush()
    db.add(
        VerificationRun(
            workspace_id=workspace_id,
            execution_run_id=execution.id,
            status="passed",
            method="deterministic-record-check",
            result={"executionRecorded": True},
            idempotency_key=f"{idempotency_key}:verification",
            provenance={"verifier": "deterministic-local", "version": "1"},
            verified_at=now,
        )
    )
    db.add(
        MeasurementWindow(
            workspace_id=workspace_id,
            action_id=action_id,
            status="scheduled",
            starts_at=now,
            ends_at=now + timedelta(days=28),
            metric_contract={
                "schemaVersion": "1",
                "source": "manual",
                "metric": "verified_outcome",
                "attributionLimit": "No causal attribution",
            },
            baseline={},
            idempotency_key=f"{idempotency_key}:measurement:28d",
            provenance={"scheduler": "deterministic-local", "version": "1"},
        )
    )
    await db.commit()
    await db.refresh(execution)
    return execution


__all__ = ["execute_action"]
