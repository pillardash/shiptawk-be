from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.detectors import ApprovedEvidenceAnalyzer, OpportunityAnalyzer
from app.modules.operator.models import OperatorRun, Opportunity
from app.modules.operator.policies import WRITE_ROLES, require_role
from app.modules.operator.schemas import OperatorRunCreate, OpportunityCreate
from app.modules.operator.services.opportunities_service import create_opportunity_record
from app.modules.operator.services.planning_service import build_plan
from app.modules.operator.services.validation_service import approved_evidence, validate_product
from app.shared.exceptions import ConflictError


async def run_operator(
    db: AsyncSession,
    workspace_id: UUID,
    actor_id: UUID,
    payload: OperatorRunCreate,
    analyzer: OpportunityAnalyzer | None = None,
) -> OperatorRun:
    await require_role(db, workspace_id, actor_id, WRITE_ROLES)
    await validate_product(db, workspace_id, payload.product_id)
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
    evidence = await approved_evidence(db, workspace_id, payload.product_id)
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
                await create_opportunity_record(
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
        plan = await build_plan(
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


__all__ = ["run_operator"]
