from decimal import Decimal

from app.modules.operator.detectors.opportunity_support_detector import candidate, rejected, stopped
from app.modules.operator.enums import (
    ActionFamily,
    EvaluationOutcome,
    EvaluationReason,
    OpportunityType,
)
from app.modules.operator.policies.data_quality_policy import quality_reasons
from app.modules.operator.policies.detector_score_policy import search_scores
from app.modules.operator.schemas.opportunity_detection_schema import (
    DetectionInput,
    DetectorEvaluation,
    QueryPerformanceSnapshot,
)

DETECTOR_ID = "search_decline"
DETECTOR_VERSION = "1"


def detect(data: DetectionInput) -> tuple[DetectorEvaluation, ...]:
    reasons = quality_reasons(data, requires_search=True)
    if reasons:
        return (stopped(DETECTOR_ID, reasons),)
    assert data.search
    accepted: dict[str, tuple[tuple[int, int, str], QueryPerformanceSnapshot]] = {}
    qualifying: list[tuple[tuple[int, int, str], QueryPerformanceSnapshot]] = []
    rejected_items: list[DetectorEvaluation] = []
    for q in sorted(data.search.queries, key=lambda x: x.query_fingerprint):
        c, p = q.current, q.prior
        clicks = (
            p.clicks >= 10
            and p.clicks - c.clicks >= 3
            and Decimal(c.clicks) <= Decimal(p.clicks) * Decimal(".70")
            and c.impressions + p.impressions >= 100
        )
        impressions = (
            p.impressions >= 200
            and c.impressions <= int(Decimal(p.impressions) * Decimal(".60"))
            and p.impressions - c.impressions >= 100
        )
        if not (clicks or impressions):
            rejected_items.append(
                rejected(DETECTOR_ID, EvaluationReason.threshold_not_met, q.query_fingerprint)
            )
            continue
        group = q.matched_page_fingerprint or f"query:{q.query_fingerprint}"
        loss = (p.clicks - c.clicks, p.impressions - c.impressions, q.query_fingerprint)
        qualifying.append((loss, q))
        if group not in accepted or loss > accepted[group][0]:
            accepted[group] = (loss, q)
    winners = {query.query_fingerprint for _, query in accepted.values()}
    out: list[DetectorEvaluation] = []
    for loss, q in qualifying:
        scores = search_scores(
            q.current,
            ActionFamily.content_refresh_investigation,
            relevance=q.profile_relevance,
            signal=min(Decimal(35), Decimal(max(loss[0], 0) * 3 + max(loss[1], 0)) / 10),
        )
        evaluation = candidate(
            data,
            DETECTOR_ID,
            OpportunityType.search_decline,
            ActionFamily.content_refresh_investigation,
            "query",
            q.query_fingerprint,
            f"Investigate search decline for {q.query_text}",
            "Review the matched page and search intent.",
            q.current,
            data.as_of,
            impact=scores["impact"],
            confidence=scores["confidence"],
            fit=scores["fit"],
            urgency=scores["urgency"],
            effort=scores["effort"],
            source_id=data.search.source_id,
            source_run_id=data.search.sync_id,
        )
        if q.query_fingerprint not in winners:
            evaluation = evaluation.model_copy(
                update={
                    "outcome": EvaluationOutcome.superseded_in_run,
                    "reason_codes": (EvaluationReason.lower_priority,),
                }
            )
        out.append(evaluation)
    return tuple(out + rejected_items) or (
        rejected(DETECTOR_ID, EvaluationReason.insufficient_evidence, "none"),
    )
