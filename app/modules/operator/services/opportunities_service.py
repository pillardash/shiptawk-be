from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import Opportunity
from app.modules.operator.policies import ACTIVE_COOLDOWN_STATUSES, WRITE_ROLES, require_role
from app.modules.operator.schemas import OpportunityCreate
from app.modules.operator.services.validation_service import validate_evidence_ids, validate_product
from app.shared.exceptions import ConflictError


async def create_opportunity_record(
    db: AsyncSession,
    workspace_id: UUID,
    actor_id: UUID,
    payload: OpportunityCreate,
    *,
    operator_run_id: UUID | None = None,
) -> Opportunity:
    await validate_product(db, workspace_id, payload.product_id)
    await validate_evidence_ids(db, workspace_id, payload.evidence_ids)
    now = datetime.now(UTC)
    existing = await db.scalar(
        select(Opportunity).where(
            Opportunity.workspace_id == workspace_id,
            Opportunity.cooldown_dedup_key == payload.cooldown_dedup_key,
            Opportunity.cooldown_until > now,
            Opportunity.status.in_(ACTIVE_COOLDOWN_STATUSES),
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
    await require_role(db, workspace_id, actor_id, WRITE_ROLES)
    opportunity = await create_opportunity_record(db, workspace_id, actor_id, payload)
    await db.commit()
    await db.refresh(opportunity)
    return opportunity


__all__ = ["create_opportunity", "create_opportunity_record"]
