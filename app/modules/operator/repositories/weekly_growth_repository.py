from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import (
    ApprovalRequest,
    MarketingPlan,
    OperatorRecommendation,
    OperatorRun,
    PlanAction,
    PreparedAsset,
)


async def get_run(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, run_id: UUID, *, lock: bool = False
) -> OperatorRun | None:
    query = select(OperatorRun).where(
        OperatorRun.workspace_id == workspace_id,
        OperatorRun.product_id == product_id,
        OperatorRun.id == run_id,
        OperatorRun.run_kind == "weekly_growth",
    )
    return cast(OperatorRun | None, await db.scalar(query.with_for_update() if lock else query))


async def get_run_by_key(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, idempotency_key: str
) -> OperatorRun | None:
    return cast(
        OperatorRun | None,
        await db.scalar(
            select(OperatorRun).where(
                OperatorRun.workspace_id == workspace_id,
                OperatorRun.product_id == product_id,
                OperatorRun.idempotency_key == idempotency_key,
            )
        ),
    )


async def get_plan(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, plan_id: UUID, *, lock: bool = False
) -> MarketingPlan | None:
    query = select(MarketingPlan).where(
        MarketingPlan.workspace_id == workspace_id,
        MarketingPlan.product_id == product_id,
        MarketingPlan.id == plan_id,
    )
    return cast(MarketingPlan | None, await db.scalar(query.with_for_update() if lock else query))


async def get_action(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, action_id: UUID, *, lock: bool = False
) -> PlanAction | None:
    query = select(PlanAction).where(
        PlanAction.workspace_id == workspace_id,
        PlanAction.product_id == product_id,
        PlanAction.id == action_id,
    )
    return cast(PlanAction | None, await db.scalar(query.with_for_update() if lock else query))


async def get_recommendation(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, recommendation_id: UUID
) -> OperatorRecommendation | None:
    return cast(
        OperatorRecommendation | None,
        await db.scalar(
            select(OperatorRecommendation).where(
                OperatorRecommendation.workspace_id == workspace_id,
                OperatorRecommendation.product_id == product_id,
                OperatorRecommendation.id == recommendation_id,
            )
        ),
    )


async def get_exact_asset(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, asset_id: UUID, revision: int
) -> PreparedAsset | None:
    return cast(
        PreparedAsset | None,
        await db.scalar(
            select(PreparedAsset).where(
                PreparedAsset.workspace_id == workspace_id,
                PreparedAsset.product_id == product_id,
                PreparedAsset.id == asset_id,
                PreparedAsset.revision == revision,
            )
        ),
    )


async def get_pending_approval(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, action_id: UUID
) -> ApprovalRequest | None:
    return cast(
        ApprovalRequest | None,
        await db.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.workspace_id == workspace_id,
                ApprovalRequest.product_id == product_id,
                ApprovalRequest.action_id == action_id,
                ApprovalRequest.status == "pending",
            )
        ),
    )


async def list_plan_actions(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, plan_id: UUID
) -> list[PlanAction]:
    return list(
        await db.scalars(
            select(PlanAction)
            .where(
                PlanAction.workspace_id == workspace_id,
                PlanAction.product_id == product_id,
                PlanAction.plan_id == plan_id,
            )
            .order_by(PlanAction.position, PlanAction.id)
        )
    )


async def flush(db: AsyncSession, *resources: object) -> None:
    db.add_all(resources)
    await db.flush()
