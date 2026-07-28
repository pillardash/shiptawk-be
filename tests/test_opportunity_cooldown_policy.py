from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.modules.operator.enums import OpportunityType
from app.modules.operator.policies.cooldown_policy import cooldown_decision, novelty_for_history
from app.modules.operator.schemas import OpportunityHistoryItem

NOW = datetime(2026, 7, 27, tzinfo=UTC)
CTR = OpportunityType.high_impressions_low_ctr
RANKING = OpportunityType.ranking_within_reach


def item(status: str, days: int = 1, **kwargs: object) -> OpportunityHistoryItem:
    return OpportunityHistoryItem(
        identity_fingerprint="x", status=status, occurred_at=NOW - timedelta(days=days), **kwargs
    )


def test_open_and_planned_are_duplicates() -> None:
    for status in ("open", "planned"):
        assert cooldown_decision(item(status), CTR, NOW)[1] is not None


def test_completed_and_dismissed_cooldowns_then_novelty_decay() -> None:
    assert cooldown_decision(item("completed", 27), CTR, NOW)[1] is not None
    assert (
        cooldown_decision(item("dismissed", 89, dismissal_reason="not_relevant"), CTR, NOW)[1]
        is not None
    )
    assert cooldown_decision(item("completed", 56), CTR, NOW) == (
        Decimal(70),
        None,
    )
    assert cooldown_decision(item("completed", 100), CTR, NOW) == (
        Decimal(50),
        None,
    )


def test_explicit_cooldown_and_empty_or_mixed_history() -> None:
    explicit = item("superseded", cooldown_until=NOW + timedelta(seconds=1))
    assert cooldown_decision(explicit, RANKING, NOW)[1] is None
    assert novelty_for_history((), RANKING, NOW) == (Decimal(100), None)
    assert novelty_for_history((item("completed", 100), item("completed", 60)), CTR, NOW) == (
        Decimal(50),
        None,
    )
