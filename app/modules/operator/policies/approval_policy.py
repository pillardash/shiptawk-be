from app.modules.operator.models import ActionRiskReview, ApprovalRequest, PlanAction, PreparedAsset


def initial_action_state(approval_level: str) -> tuple[str, str]:
    if approval_level == "safe":
        return "not_required", "ready"
    return "pending", "pending"


def action_has_required_approval(
    action: PlanAction,
    approval: ApprovalRequest | None,
    asset: PreparedAsset | None = None,
    review: ActionRiskReview | None = None,
) -> bool:
    if action.product_id is None:
        return action.approval_level == "safe" or (
            approval is not None and approval.status == "approved"
        )
    return (
        approval is not None
        and approval.status == "approved"
        and asset is not None
        and review is not None
        and approval.product_id == action.product_id == asset.product_id == review.product_id
        and approval.asset_id == asset.id == review.asset_id
        and approval.asset_revision == asset.revision == review.asset_revision
        and asset.action_id == action.id
        and review.final_outcome != "block"
    )


def action_state_after_decision(decision: str) -> str:
    return "ready" if decision == "approved" else "cancelled"
