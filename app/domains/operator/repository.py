from typing import cast
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.domains.workspaces.models import WorkspaceMembership

type OperatorResource = (
    Opportunity
    | MarketingPlan
    | PlanAction
    | ApprovalRequest
    | ExecutionRun
    | VerificationRun
    | MeasurementWindow
    | OperatorRun
)


async def has_workspace_access(db: AsyncSession, workspace_id: UUID, user_id: UUID) -> bool:
    return (
        await db.scalar(
            select(WorkspaceMembership.workspace_id).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.is_active.is_(True),
            )
        )
        is not None
    )


async def list_resources[T: OperatorResource](
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    model: type[T],
    *,
    statement: Select[tuple[T]] | None = None,
) -> list[T] | None:
    if not await has_workspace_access(db, workspace_id, user_id):
        return None
    query = statement if statement is not None else select(model)
    return cast(list[T], list(await db.scalars(query.where(model.workspace_id == workspace_id))))


async def get_resource[T: OperatorResource](
    db: AsyncSession,
    workspace_id: UUID,
    resource_id: UUID,
    user_id: UUID,
    model: type[T],
) -> T | None:
    if not await has_workspace_access(db, workspace_id, user_id):
        return None
    return cast(
        T | None,
        await db.scalar(
            select(model).where(model.workspace_id == workspace_id, model.id == resource_id)
        ),
    )


async def get_plan_with_actions(
    db: AsyncSession, workspace_id: UUID, plan_id: UUID, user_id: UUID
) -> tuple[MarketingPlan, list[PlanAction]] | None:
    plan = await get_resource(db, workspace_id, plan_id, user_id, MarketingPlan)
    if plan is None:
        return None
    actions = list(
        await db.scalars(
            select(PlanAction)
            .where(PlanAction.workspace_id == workspace_id, PlanAction.plan_id == plan_id)
            .order_by(PlanAction.created_at, PlanAction.id)
        )
    )
    return plan, actions


async def get_current_plan(
    db: AsyncSession, workspace_id: UUID, user_id: UUID
) -> tuple[MarketingPlan, list[PlanAction]] | None:
    if not await has_workspace_access(db, workspace_id, user_id):
        return None
    plan = await db.scalar(
        select(MarketingPlan)
        .where(MarketingPlan.workspace_id == workspace_id, MarketingPlan.status == "active")
        .order_by(MarketingPlan.period_start.desc(), MarketingPlan.id.desc())
    )
    if plan is None:
        return None
    return await get_plan_with_actions(db, workspace_id, plan.id, user_id)
