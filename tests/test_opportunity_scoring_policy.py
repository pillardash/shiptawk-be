from decimal import Decimal

from app.modules.operator.policies.scoring_policy import (
    confidence_label,
    score_gate,
    score_opportunity,
)


def test_scoring_v1_uses_normalized_factors_and_half_up_integer_rounding() -> None:
    scores = score_opportunity(
        impact=Decimal("70"),
        confidence=Decimal("80"),
        effort=Decimal("30"),
        urgency=Decimal("60"),
        strategic_fit=Decimal("75"),
        novelty=Decimal("100"),
    )
    assert scores.priority == Decimal("52")


def test_scoring_clamps_inputs_and_priority() -> None:
    scores = score_opportunity(
        impact=Decimal("200"),
        confidence=Decimal("200"),
        effort=Decimal("-1"),
        urgency=Decimal("200"),
        strategic_fit=Decimal("200"),
        novelty=Decimal("200"),
    )
    assert scores.priority == Decimal("100")
    assert scores.effort == Decimal("0")


def test_scoring_rounds_half_up() -> None:
    scores = score_opportunity(
        impact=Decimal("50.5"),
        confidence=Decimal("100"),
        effort=Decimal("0"),
        urgency=Decimal("0"),
        strategic_fit=Decimal("100"),
        novelty=Decimal("100"),
    )
    assert scores.priority == Decimal("51")


def test_confidence_and_score_gate_boundaries() -> None:
    assert [confidence_label(Decimal(value)).value for value in (59, 60, 79, 80)] == [
        "low",
        "medium",
        "medium",
        "high",
    ]
    scores = score_opportunity(
        impact=Decimal(50),
        confidence=Decimal(59),
        effort=Decimal(50),
        urgency=Decimal(50),
        strategic_fit=Decimal(49),
        novelty=Decimal(50),
    )
    assert {reason.value for reason in score_gate(scores)} == {
        "confidence_below_threshold",
        "strategic_fit_below_threshold",
    }
