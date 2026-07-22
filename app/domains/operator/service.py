from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.models import EvidenceItem
from app.domains.operator.analyzers import ApprovedEvidenceAnalyzer, OpportunityAnalyzer
from app.domains.operator.models import (
    ApprovalRequest,
    ExecutionRun,
    MarketingPlan,
    MeasurementWindow,
    OperatorRun,
    Opportunity,
    PlanAction,
    VerificationRun,
)
from app.domains.operator.schemas import OperatorRunCreate, OpportunityCreate, PlanCreate
from app.domains.products.models import Product
from app.domains.workspaces.models import WorkspaceMembership, WorkspaceRole
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError

WRITE_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor}
REVIEW_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.reviewer}


async def _require_role(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, allowed: set[WorkspaceRole]
) -> None:
    role = await db.scalar(
        select(WorkspaceMembership.role).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.is_active.is_(True),
        )
    )
    if role is None:
        raise NotFoundError("Operator resource not found.", code="operator_resource_not_found")
    if role not in allowed:
        raise ForbiddenError(
            "The workspace role cannot perform this operator action.",
            code="operator_write_forbidden",
        )


async def _validate_product(db: AsyncSession, workspace_id: UUID, product_id: UUID | None) -> None:
    if product_id is None:
        return
    product = await db.scalar(
        select(Product.id).where(Product.workspace_id == workspace_id, Product.id == product_id)
    )
    if product is None:
        raise NotFoundError("Product not found.", code="product_not_found")


async def _approved_evidence(
    db: AsyncSession, workspace_id: UUID, product_id: UUID | None
) -> list[EvidenceItem]:
    query = select(EvidenceItem).where(
        EvidenceItem.workspace_id == workspace_id,
        EvidenceItem.status == "approved",
        EvidenceItem.classification.notin_(["blocked", "confidential"]),
    )
    if product_id is not None:
        query = query.where(EvidenceItem.product_id == product_id)
    return list(await db.scalars(query.order_by(EvidenceItem.observed_at.desc(), EvidenceItem.id)))


async def _validate_evidence_ids(
    db: AsyncSession, workspace_id: UUID, evidence_ids: list[UUID]
) -> None:
    found = set(
        await db.scalars(
            select(EvidenceItem.id).where(
                EvidenceItem.workspace_id == workspace_id,
                EvidenceItem.id.in_(evidence_ids),
                EvidenceItem.status == "approved",
                EvidenceItem.classification.notin_(["blocked", "confidential"]),
            )
        )
    )
    if found != set(evidence_ids):
        raise ConflictError(
            "Recommendations may reference only approved workspace evidence.",
            code="recommendation_evidence_invalid",
        )


async def _create_opportunity(
    db: AsyncSession,
    workspace_id: UUID,
    actor_id: UUID,
    payload: OpportunityCreate,
    *,
    operator_run_id: UUID | None = None,
) -> Opportunity:
    await _validate_product(db, workspace_id, payload.product_id)
    await _validate_evidence_ids(db, workspace_id, payload.evidence_ids)
    now = datetime.now(UTC)
    existing = await db.scalar(
        select(Opportunity).where(
            Opportunity.workspace_id == workspace_id,
            Opportunity.cooldown_dedup_key == payload.cooldown_dedup_key,
            Opportunity.cooldown_until > now,
            Opportunity.status.in_(["open", "planned", "completed"]),
        )
    )
    if existing is not None:
        raise ConflictError(
            "An equivalent opportunity is inside its cooldown window.",
            code="opportunity_cooldown_active",
        )
    opportunity = Opportunity(
        workspace_id=workspace_id,
        product_id=payload.product_id,
        operator_run_id=operator_run_id,
        title=payload.title,
        evidence_ids=[str(value) for value in payload.evidence_ids],
        interpretation=payload.interpretation,
        recommendation=payload.recommendation,
        confidence=payload.confidence.value,
        missing_information=payload.missing_information,
        approval_level=payload.approval_level.value,
        measurement_window=payload.measurement_window,
        stop_conditions=payload.stop_conditions,
        impact_score=payload.impact_score,
        effort_score=payload.effort_score,
        urgency_score=payload.urgency_score,
        cooldown_dedup_key=payload.cooldown_dedup_key,
        cooldown_until=now + timedelta(days=payload.cooldown_days),
        provenance=payload.provenance,
        created_by_actor_id=actor_id,
    )
    db.add(opportunity)
    await db.flush()
    return opportunity


async def create_opportunity(
    db: AsyncSession, workspace_id: UUID, actor_id: UUID, payload: OpportunityCreate
) -> Opportunity:
    await _require_role(db, workspace_id, actor_id, WRITE_ROLES)
    opportunity = await _create_opportunity(db, workspace_id, actor_id, payload)
    await db.commit()
    await db.refresh(opportunity)
    return opportunity


async def _build_plan(
    db: AsyncSession,
    workspace_id: UUID,
    actor_id: UUID,
    title: str,
    opportunities: list[Opportunity],
    idempotency_key: str,
    provenance: dict[str, object],
    operator_run_id: UUID | None = None,
) -> MarketingPlan:
    existing = await db.scalar(
        select(MarketingPlan).where(
            MarketingPlan.workspace_id == workspace_id,
            MarketingPlan.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing
    now = datetime.now(UTC)
    plan = MarketingPlan(
        workspace_id=workspace_id,
        operator_run_id=operator_run_id,
        title=title,
        status="active",
        period_start=now,
        period_end=now + timedelta(days=7),
        idempotency_key=idempotency_key,
        provenance=provenance,
        created_by_actor_id=actor_id,
    )
    db.add(plan)
    await db.flush()
    for opportunity in opportunities[:3]:
        approval_status = "not_required" if opportunity.approval_level == "safe" else "pending"
        action = PlanAction(
            workspace_id=workspace_id,
            plan_id=plan.id,
            opportunity_id=opportunity.id,
            title=opportunity.title,
            description=opportunity.recommendation,
            evidence_ids=opportunity.evidence_ids,
            confidence=opportunity.confidence,
            missing_information=opportunity.missing_information,
            approval_level=opportunity.approval_level,
            approval_status=approval_status,
            status="ready" if approval_status == "not_required" else "pending",
            cooldown_dedup_key=opportunity.cooldown_dedup_key,
            idempotency_key=f"{idempotency_key}:action:{opportunity.id}",
            provenance=opportunity.provenance,
        )
        db.add(action)
        opportunity.status = "planned"
        await db.flush()
        if approval_status == "pending":
            db.add(
                ApprovalRequest(
                    workspace_id=workspace_id,
                    action_id=action.id,
                    status="pending",
                    requested_by_actor_id=actor_id,
                    provenance={"source": "plan-builder", **provenance},
                )
            )
    await db.flush()
    return plan


async def create_plan(
    db: AsyncSession, workspace_id: UUID, actor_id: UUID, payload: PlanCreate
) -> MarketingPlan:
    await _require_role(db, workspace_id, actor_id, WRITE_ROLES)
    opportunities = list(
        await db.scalars(
            select(Opportunity).where(
                Opportunity.workspace_id == workspace_id,
                Opportunity.id.in_(payload.opportunity_ids),
                Opportunity.status == "open",
            )
        )
    )
    if len(opportunities) != len(set(payload.opportunity_ids)):
        raise NotFoundError("Opportunity not found.", code="opportunity_not_found")
    plan = await _build_plan(
        db,
        workspace_id,
        actor_id,
        payload.title,
        opportunities,
        payload.idempotency_key,
        {"builder": "manual", "version": "1"},
    )
    await db.commit()
    await db.refresh(plan)
    return plan


async def run_operator(
    db: AsyncSession,
    workspace_id: UUID,
    actor_id: UUID,
    payload: OperatorRunCreate,
    analyzer: OpportunityAnalyzer | None = None,
) -> OperatorRun:
    await _require_role(db, workspace_id, actor_id, WRITE_ROLES)
    await _validate_product(db, workspace_id, payload.product_id)
    existing = await db.scalar(
        select(OperatorRun).where(
            OperatorRun.workspace_id == workspace_id,
            OperatorRun.idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        return existing
    selected_analyzer = analyzer or ApprovedEvidenceAnalyzer()
    run = OperatorRun(
        workspace_id=workspace_id,
        product_id=payload.product_id,
        trigger=payload.trigger,
        status="running",
        idempotency_key=payload.idempotency_key,
        analyzer_version=selected_analyzer.version,
        provenance=selected_analyzer.provenance,
        summary={},
        created_by_actor_id=actor_id,
    )
    db.add(run)
    await db.flush()
    evidence = await _approved_evidence(db, workspace_id, payload.product_id)
    analyzed = selected_analyzer.analyze(evidence)
    opportunities: list[Opportunity] = []
    deduplicated = 0
    for item in analyzed[:3]:
        opportunity_payload = OpportunityCreate(
            product_id=payload.product_id,
            title=item.title,
            evidence_ids=item.evidence_ids,
            interpretation=item.interpretation,
            recommendation=item.recommendation,
            confidence=item.confidence,
            missing_information=item.missing_information,
            approval_level=item.approval_level,
            measurement_window=item.measurement_window,
            stop_conditions=item.stop_conditions,
            impact_score=item.impact_score,
            effort_score=item.effort_score,
            urgency_score=item.urgency_score,
            cooldown_dedup_key=item.cooldown_dedup_key,
            cooldown_days=28,
            provenance=selected_analyzer.provenance,
        )
        try:
            opportunities.append(
                await _create_opportunity(
                    db,
                    workspace_id,
                    actor_id,
                    opportunity_payload,
                    operator_run_id=run.id,
                )
            )
        except ConflictError as exc:
            if exc.code != "opportunity_cooldown_active":
                raise
            deduplicated += 1
    plan = None
    if opportunities:
        plan = await _build_plan(
            db,
            workspace_id,
            actor_id,
            "Weekly marketing plan",
            opportunities,
            f"operator-run:{run.id}",
            selected_analyzer.provenance,
            run.id,
        )
    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    run.summary = {
        "approvedEvidence": len(evidence),
        "createdOpportunities": len(opportunities),
        "deduplicatedOpportunities": deduplicated,
        "planId": str(plan.id) if plan else None,
        "missingInformation": [] if evidence else ["approved evidence"],
    }
    await db.commit()
    await db.refresh(run)
    return run


async def decide_action(
    db: AsyncSession,
    workspace_id: UUID,
    plan_id: UUID,
    action_id: UUID,
    actor_id: UUID,
    decision: str,
    reason: str | None,
) -> ApprovalRequest:
    await _require_role(db, workspace_id, actor_id, REVIEW_ROLES)
    action = await db.scalar(
        select(PlanAction).where(
            PlanAction.workspace_id == workspace_id,
            PlanAction.plan_id == plan_id,
            PlanAction.id == action_id,
        )
    )
    if action is None:
        raise NotFoundError("Plan action not found.", code="plan_action_not_found")
    approval = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.workspace_id == workspace_id,
            ApprovalRequest.action_id == action_id,
        )
    )
    if approval is None:
        raise NotFoundError("Approval request not found.", code="approval_request_not_found")
    if approval.status != "pending" and approval.status != decision:
        raise ConflictError("Approval was already decided.", code="approval_already_decided")
    approval.status = decision
    approval.reason = reason
    approval.decided_by_actor_id = actor_id
    approval.decided_at = datetime.now(UTC)
    action.approval_status = decision
    action.status = "ready" if decision == "approved" else "cancelled"
    await db.commit()
    await db.refresh(approval)
    return approval


async def execute_action(
    db: AsyncSession,
    workspace_id: UUID,
    plan_id: UUID,
    action_id: UUID,
    actor_id: UUID,
    idempotency_key: str,
) -> ExecutionRun:
    await _require_role(db, workspace_id, actor_id, WRITE_ROLES)
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
    if action.approval_level != "safe" and (approval is None or approval.status != "approved"):
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
