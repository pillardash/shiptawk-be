from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.operator.models import ApprovalRequest, PlanAction
from app.modules.operator.policies.approval_policy import (
    action_has_required_approval,
    action_state_after_decision,
    initial_action_state,
)
from app.modules.operator.services import approvals_service
from app.shared.exceptions import ConflictError, NotFoundError


@pytest.mark.asyncio
async def test_legacy_approval_decision_has_typed_failures_and_one_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(approvals_service, "require_role", AsyncMock())
    workspace_id, plan_id, action_id, actor_id = uuid4(), uuid4(), uuid4(), uuid4()

    missing_action = AsyncMock()
    missing_action.scalar.return_value = None
    with pytest.raises(NotFoundError, match="Plan action"):
        await approvals_service.decide_action(
            missing_action, workspace_id, plan_id, action_id, actor_id, "approved", None
        )

    action = cast(PlanAction, SimpleNamespace(approval_status="pending", status="pending"))
    missing_approval = AsyncMock()
    missing_approval.scalar.side_effect = [action, None]
    with pytest.raises(NotFoundError, match="Approval request"):
        await approvals_service.decide_action(
            missing_approval, workspace_id, plan_id, action_id, actor_id, "approved", None
        )

    decided = SimpleNamespace(status="rejected")
    conflict = AsyncMock()
    conflict.scalar.side_effect = [action, decided]
    with pytest.raises(ConflictError, match="already decided"):
        await approvals_service.decide_action(
            conflict, workspace_id, plan_id, action_id, actor_id, "approved", None
        )

    approval = cast(
        ApprovalRequest,
        SimpleNamespace(
            status="pending",
            reason=None,
            decided_by_actor_id=None,
            decided_at=None,
        ),
    )
    success = AsyncMock()
    success.scalar.side_effect = [action, approval]
    result = await approvals_service.decide_action(
        success, workspace_id, plan_id, action_id, actor_id, "approved", "Reviewed"
    )
    assert result is approval
    assert (approval.status, approval.reason, approval.decided_by_actor_id) == (
        "approved",
        "Reviewed",
        actor_id,
    )
    assert (action.approval_status, action.status) == ("approved", "ready")
    success.commit.assert_awaited_once()
    success.refresh.assert_awaited_once_with(approval)


def test_public_approval_policy_distinguishes_recommendation_and_execution_authority() -> None:
    assert initial_action_state("safe") == ("not_required", "ready")
    assert initial_action_state("review") == ("pending", "pending")
    assert action_state_after_decision("approved") == "ready"
    assert action_state_after_decision("rejected") == "cancelled"

    safe = cast(PlanAction, SimpleNamespace(product_id=None, approval_level="safe"))
    review = cast(PlanAction, SimpleNamespace(product_id=None, approval_level="review"))
    approved = cast(ApprovalRequest, SimpleNamespace(status="approved"))
    assert action_has_required_approval(safe, None)
    assert action_has_required_approval(review, approved)
    assert not action_has_required_approval(review, None)
