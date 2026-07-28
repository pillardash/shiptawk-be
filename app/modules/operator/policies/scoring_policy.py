from decimal import ROUND_HALF_UP, Decimal

from app.modules.operator.enums import Confidence, EvaluationReason
from app.modules.operator.schemas.opportunity_detection_schema import ScoreFeatures

SCORING_VERSION = "v1"


def confidence_label(score: Decimal) -> Confidence:
    if score >= 80:
        return Confidence.high
    if score >= 60:
        return Confidence.medium
    return Confidence.low


def score_opportunity(
    *,
    impact: Decimal,
    confidence: Decimal,
    effort: Decimal,
    urgency: Decimal,
    strategic_fit: Decimal,
    novelty: Decimal,
) -> ScoreFeatures:
    values = (impact, confidence, effort, urgency, strategic_fit, novelty)
    bounded = tuple(max(Decimal(0), min(Decimal(100), value)) for value in values)
    impact, confidence, effort, urgency, strategic_fit, novelty = bounded
    impact_n, confidence_n, effort_n, urgency_n, fit_n, novelty_n = (
        value / Decimal(100) for value in bounded
    )
    urgency_factor = Decimal(".50") + Decimal(".50") * urgency_n
    effort_factor = Decimal(".50") + Decimal(".50") * effort_n
    product = impact_n * confidence_n * fit_n * novelty_n * urgency_factor
    priority = (product / effort_factor * Decimal(100)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    priority = max(Decimal(0), min(Decimal(100), priority))
    return ScoreFeatures(
        impact=impact,
        confidence=confidence,
        effort=effort,
        urgency=urgency,
        strategic_fit=strategic_fit,
        novelty=novelty,
        priority=priority,
    )


def score_gate(scores: ScoreFeatures) -> tuple[EvaluationReason, ...]:
    reasons: list[EvaluationReason] = []
    if scores.confidence < 60:
        reasons.append(EvaluationReason.confidence_below_threshold)
    if scores.strategic_fit < 50:
        reasons.append(EvaluationReason.strategic_fit_below_threshold)
    return tuple(reasons)
