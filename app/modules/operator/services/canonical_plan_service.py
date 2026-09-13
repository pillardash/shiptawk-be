from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import MarketingPlan, OperatorRun


@dataclass(frozen=True, slots=True)
class CanonicalPlan:
    run: OperatorRun | None
    plan: MarketingPlan | None


async def resolve_canonical_plan(
    db: AsyncSession, *, workspace_id: UUID, product_id: UUID
) -> CanonicalPlan:
    """Return the current run and latest finalized report independently."""
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
        .order_by(MarketingPlan.finalized_at.desc(), MarketingPlan.plan_revision.desc())
        .limit(1)
    )
    return CanonicalPlan(run=run, plan=plan)
