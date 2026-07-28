from decimal import Decimal

from app.modules.operator.detectors.opportunity_support_detector import candidate, rejected, stopped
from app.modules.operator.enums import ActionFamily, EffortBand, EvaluationReason, OpportunityType
from app.modules.operator.policies.data_quality_policy import quality_reasons
from app.modules.operator.policies.detector_score_policy import search_scores
from app.modules.operator.schemas.opportunity_detection_schema import (
    DetectionInput,
    DetectorEvaluation,
)

DETECTOR_ID = "search_demand_without_dedicated_content"
DETECTOR_VERSION = "1"


def detect(data: DetectionInput) -> tuple[DetectorEvaluation, ...]:
    reasons = quality_reasons(data, requires_search=True, requires_website=True)
    if reasons:
        return (stopped(DETECTOR_ID, reasons),)
    assert data.search and data.website
    out = []
    for q in sorted(data.search.queries, key=lambda x: x.query_fingerprint):
        if q.current.impressions < 50 or q.profile_relevance < Decimal(".20"):
            reason = EvaluationReason.threshold_not_met
        elif q.branded or q.navigational:
            reason = EvaluationReason.navigation_or_branded_query
        elif q.page_match_status == "ambiguous":
            reason = EvaluationReason.target_page_ambiguous
        elif q.page_match_status != "unmatched":
            reason = EvaluationReason.threshold_not_met
        else:
            scores = search_scores(
                q.current,
                ActionFamily.dedicated_content_creation,
                relevance=q.profile_relevance,
                signal=min(Decimal(30), Decimal(q.current.impressions) / 20),
            )
            out.append(
                candidate(
                    data,
                    DETECTOR_ID,
                    OpportunityType.search_demand_without_dedicated_content,
                    ActionFamily.dedicated_content_creation,
                    "query",
                    q.query_fingerprint,
                    f"Create dedicated content for {q.query_text}",
                    "Prepare a dedicated evidence-backed content brief.",
                    q.current,
                    data.as_of,
                    impact=scores["impact"],
                    confidence=scores["confidence"],
                    fit=scores["fit"],
                    urgency=scores["urgency"],
                    effort=scores["effort"],
                    source_id=data.search.source_id,
                    source_run_id=data.search.sync_id,
                    effort_band=EffortBand.large,
                )
            )
            continue
        out.append(rejected(DETECTOR_ID, reason, q.query_fingerprint))
    return tuple(out) or (rejected(DETECTOR_ID, EvaluationReason.insufficient_evidence, "none"),)
