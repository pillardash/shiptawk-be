from decimal import ROUND_FLOOR, Decimal

from app.modules.operator.detectors.opportunity_support_detector import candidate, rejected, stopped
from app.modules.operator.enums import ActionFamily, EvaluationReason, OpportunityType
from app.modules.operator.policies.data_quality_policy import quality_reasons
from app.modules.operator.policies.detector_score_policy import search_scores
from app.modules.operator.schemas.opportunity_detection_schema import (
    DetectionInput,
    DetectorEvaluation,
)

DETECTOR_ID = "high_impressions_low_ctr"
DETECTOR_VERSION = "1"


def detect(data: DetectionInput) -> tuple[DetectorEvaluation, ...]:
    reasons = quality_reasons(data, requires_search=True)
    if reasons:
        return (stopped(DETECTOR_ID, reasons),)
    assert data.search is not None
    output = []
    for query in sorted(data.search.queries, key=lambda item: item.query_fingerprint):
        m = query.current
        shortfall = (
            int((Decimal(m.impressions) * Decimal(".02")).to_integral_value(rounding=ROUND_FLOOR))
            - m.clicks
        )
        if (
            m.impressions < 100
            or not (Decimal(1) <= m.average_position <= 10)
            or m.ctr >= Decimal(".02")
            or shortfall < 2
        ):
            output.append(
                rejected(DETECTOR_ID, EvaluationReason.threshold_not_met, query.query_fingerprint)
            )
        elif query.page_match_status != "matched" or not query.matched_page_fingerprint:
            output.append(
                rejected(
                    DETECTOR_ID, EvaluationReason.target_page_ambiguous, query.query_fingerprint
                )
            )
        else:
            scores = search_scores(
                m,
                ActionFamily.seo_snippet_update,
                relevance=query.profile_relevance,
                signal=min(Decimal(30), Decimal(shortfall * 4)),
            )
            output.append(
                candidate(
                    data,
                    DETECTOR_ID,
                    OpportunityType.high_impressions_low_ctr,
                    ActionFamily.seo_snippet_update,
                    "query",
                    query.query_fingerprint,
                    f"Improve the search snippet for {query.query_text}",
                    "Update the title and meta description.",
                    m,
                    data.as_of,
                    impact=scores["impact"],
                    confidence=scores["confidence"],
                    fit=scores["fit"],
                    urgency=scores["urgency"],
                    effort=scores["effort"],
                    source_id=data.search.source_id,
                    source_run_id=data.search.sync_id,
                )
            )
    return tuple(output) or (rejected(DETECTOR_ID, EvaluationReason.insufficient_evidence, "none"),)
