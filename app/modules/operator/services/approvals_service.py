from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import ApprovalRequest, PlanAction
from app.modules.operator.policies import REVIEW_ROLES, action_state_after_decision, require_role
from app.shared.exceptions import ConflictError, NotFoundError


async def decide_action(
    db: AsyncSession,
    workspace_id: UUID,
    plan_id: UUID,
    action_id: UUID,
    actor_id: UUID,
    decision: str,
    reason: str | None,
) -> ApprovalRequest:
    await require_role(db, workspace_id, actor_id, REVIEW_ROLES)
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
    action.status = action_state_after_decision(decision)
    await db.commit()
    await db.refresh(approval)
    return approval


__all__ = ["decide_action"]
