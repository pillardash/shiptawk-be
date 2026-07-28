from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import ApprovalRequest, MarketingPlan, Opportunity, PlanAction
from app.modules.operator.policies import WRITE_ROLES, initial_action_state, require_role
from app.modules.operator.schemas import PlanCreate
from app.shared.exceptions import NotFoundError


async def build_plan(
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
        approval_status, action_status = initial_action_state(opportunity.approval_level)
        action = PlanAction(
            workspace_id=workspace_id,
            product_id=opportunity.product_id,
            plan_id=plan.id,
            opportunity_id=opportunity.id,
            title=opportunity.title,
            description=opportunity.recommendation,
            evidence_ids=opportunity.evidence_ids,
            confidence=opportunity.confidence,
            missing_information=opportunity.missing_information,
            approval_level=opportunity.approval_level,
            approval_status=approval_status,
            status=action_status,
            cooldown_dedup_key=opportunity.cooldown_dedup_key,
            idempotency_key=f"{idempotency_key}:action:{opportunity.id}",
            provenance=opportunity.provenance,
        )
        db.add(action)
        opportunity.status = "planned"
        await db.flush()
        if approval_status == "pending" and opportunity.product_id is None:
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
    await require_role(db, workspace_id, actor_id, WRITE_ROLES)
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
    plan = await build_plan(
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


__all__ = ["build_plan", "create_plan"]
