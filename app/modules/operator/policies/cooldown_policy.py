from datetime import datetime, timedelta
from decimal import Decimal

from app.modules.operator.enums import EvaluationReason, OpportunityStatus, OpportunityType
from app.modules.operator.schemas.opportunity_detection_schema import OpportunityHistoryItem

ACTIVE_COOLDOWN_STATUSES = ("open", "planned")
COOLDOWN_POLICY_VERSION = "v1"
COMPLETED_DAYS = {
    OpportunityType.high_impressions_low_ctr: 56,
    OpportunityType.ranking_within_reach: 56,
    OpportunityType.search_decline: 28,
    OpportunityType.shipped_but_not_marketed: 84,
    OpportunityType.search_demand_without_dedicated_content: 90,
    OpportunityType.existing_page_needs_improvement: 56,
}
DISMISSAL_DAYS = {
    "not_relevant": 90,
    "already_done": 180,
    "not_now": 28,
    "insufficient_evidence": 56,
    "incorrect_interpretation": 56,
    "too_much_effort": 56,
    "duplicate": 90,
    "wrong_target_page": 56,
    "other": 56,
}


def cooldown_decision(
    item: OpportunityHistoryItem, opportunity_type: OpportunityType, as_of: datetime
) -> tuple[Decimal, EvaluationReason | None]:
    if item.status in (OpportunityStatus.open, OpportunityStatus.planned):
        return Decimal(0), EvaluationReason.duplicate
    if item.status == OpportunityStatus.superseded:
        return Decimal(100), None
    until = item.cooldown_until
    if until is None and item.status == OpportunityStatus.completed:
        until = item.occurred_at + timedelta(days=COMPLETED_DAYS[opportunity_type])
    if until is None and item.status == OpportunityStatus.dismissed:
        until = item.occurred_at + timedelta(
            days=DISMISSAL_DAYS.get(item.dismissal_reason or "", 56)
        )
    if until is not None and as_of < until:
        return Decimal(0), EvaluationReason.cooldown_active
    age = (as_of - item.occurred_at).days
    return (Decimal(70) if age < 90 else Decimal(50)), None


def novelty_for_history(
    items: tuple[OpportunityHistoryItem, ...], opportunity_type: OpportunityType, as_of: datetime
) -> tuple[Decimal, EvaluationReason | None]:
    if not items:
        return Decimal(100), None
    decisions = [cooldown_decision(item, opportunity_type, as_of) for item in items]
    blocked = next((reason for _, reason in decisions if reason), None)
    return (Decimal(0), blocked) if blocked else (min(score for score, _ in decisions), None)
