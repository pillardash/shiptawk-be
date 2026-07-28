from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import String, Uuid

from app.modules.operator.enums import OpportunityType
from app.modules.operator.models import (
    OpportunityEvaluation,
    OpportunityFeedback,
    OpportunityStatusEvent,
)
from app.modules.operator.policies.cooldown_policy import (
    COMPLETED_DAYS,
    DISMISSAL_DAYS,
    cooldown_decision,
)
from app.modules.operator.policies.opportunity_transition_policy import (
    InvalidOpportunityTransitionError,
    require_transition,
)
from app.modules.operator.schemas import OpportunityHistoryItem

NOW = datetime(2026, 7, 27, tzinfo=UTC)


def test_phase4_column_types_and_lengths() -> None:
    assert isinstance(OpportunityFeedback.__table__.c.actor_id.type, Uuid)
    assert isinstance(OpportunityStatusEvent.__table__.c.actor_id.type, Uuid)
    identity = OpportunityEvaluation.__table__.c.identity_fingerprint.type
    assert isinstance(identity, String)
    assert identity.length == 71
    assert not OpportunityEvaluation.__table__.c.evaluation_key.nullable


def test_exact_cooldown_matrix_and_superseded_never_blocks() -> None:
    assert {
        OpportunityType.high_impressions_low_ctr: 56,
        OpportunityType.ranking_within_reach: 56,
        OpportunityType.search_decline: 28,
        OpportunityType.shipped_but_not_marketed: 84,
        OpportunityType.search_demand_without_dedicated_content: 90,
        OpportunityType.existing_page_needs_improvement: 56,
    } == COMPLETED_DAYS
    assert DISMISSAL_DAYS == {
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
    item = OpportunityHistoryItem(
        identity_fingerprint="sha256:" + "a" * 64,
        status="superseded",
        occurred_at=NOW,
        cooldown_until=NOW + timedelta(days=999),
    )
    assert cooldown_decision(item, OpportunityType.search_decline, NOW)[1] is None


def test_only_open_can_be_dismissed_and_supersede_is_internal() -> None:
    require_transition("open", "dismissed", internal=False)
    with pytest.raises(InvalidOpportunityTransitionError):
        require_transition("planned", "dismissed", internal=False)
    with pytest.raises(InvalidOpportunityTransitionError):
        require_transition("open", "superseded", internal=False)
    require_transition("open", "superseded", internal=True)


def test_actor_annotation_is_uuid() -> None:
    assert "UUID" in str(OpportunityFeedback.__annotations__["actor_id"])
    assert "UUID" in str(OpportunityStatusEvent.__annotations__["actor_id"])
