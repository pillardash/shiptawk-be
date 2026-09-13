from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.services.product_event_service import write_product_event
from app.modules.operator.models import MarketingPlan, OperatorRun, PlanView
from app.modules.products.models import Product


async def record_plan_view(
    db: AsyncSession, *, workspace_id: UUID, product_id: UUID, actor_id: UUID, plan: MarketingPlan
) -> tuple[PlanView, bool]:
    revision = plan.plan_revision or 1
    # Serialize ordinal assignment per product so concurrent first views cannot duplicate ordinals.
    await db.scalar(
        select(Product.id)
        .where(Product.workspace_id == workspace_id, Product.id == product_id)
        .with_for_update()
    )
    existing = await db.scalar(
        select(PlanView).where(
            PlanView.workspace_id == workspace_id,
            PlanView.product_id == product_id,
            PlanView.actor_id == actor_id,
            PlanView.period_start == plan.period_start,
            PlanView.period_end == plan.period_end,
        )
    )
    if existing:
        return existing, True
    if plan.status != "ready":
        raise ValueError("Plan is not a finalized weekly report.")
    valid = await db.scalar(
        select(OperatorRun.id).where(
            OperatorRun.workspace_id == workspace_id,
            OperatorRun.product_id == product_id,
            OperatorRun.id == plan.operator_run_id,
            OperatorRun.run_kind == "weekly_growth",
            OperatorRun.status == "completed",
        )
    )
    if valid is None:
        raise ValueError("Plan is not a finalized weekly report.")
    ordinal = (
        int(
            await db.scalar(
                select(func.count())
                .select_from(PlanView)
                .where(
                    PlanView.workspace_id == workspace_id,
                    PlanView.product_id == product_id,
                    PlanView.actor_id == actor_id,
                )
            )
            or 0
        )
        + 1
    )
    row = PlanView(
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=actor_id,
        plan_id=plan.id,
        plan_revision=revision,
        period_start=plan.period_start,
        period_end=plan.period_end,
        report_ordinal=ordinal,
        second_report_return=ordinal == 2,
        fourth_report_return=ordinal == 4,
        valid_no_recommendation_report=(plan.action_count or 0) == 0
        and (plan.no_recommendation_count or 0) > 0,
    )
    db.add(row)
    await db.flush()
    await write_product_event(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=actor_id,
        event_name="weekly_report_viewed",
        idempotency_key=(
            f"plan-view:{product_id}:{plan.period_start.isoformat()}:{plan.period_end.isoformat()}"
        ),
        resource_type="weekly_report",
        resource_id=plan.id,
        resource_revision=revision,
        report_ordinal=ordinal,
        second_report_return=row.second_report_return,
        fourth_report_return=row.fourth_report_return,
        valid_no_recommendation_report=row.valid_no_recommendation_report,
    )
    await db.commit()
    await db.refresh(row)
    return row, False
