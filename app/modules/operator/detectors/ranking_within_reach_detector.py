from decimal import Decimal

from app.modules.operator.detectors.opportunity_support_detector import candidate, rejected, stopped
from app.modules.operator.enums import ActionFamily, EvaluationReason, OpportunityType
from app.modules.operator.policies.data_quality_policy import quality_reasons
from app.modules.operator.policies.detector_score_policy import search_scores
from app.modules.operator.schemas.opportunity_detection_schema import (
    DetectionInput,
    DetectorEvaluation,
    WebsitePageSnapshot,
)

DETECTOR_ID = "ranking_within_reach"
DETECTOR_VERSION = "1"


def detect(data: DetectionInput) -> tuple[DetectorEvaluation, ...]:
    reasons = quality_reasons(data, requires_search=True, requires_website=True)
    if reasons:
        return (stopped(DETECTOR_ID, reasons),)
    assert data.search and data.website
    pages = {
        p.page_fingerprint: p for p in data.website.pages if isinstance(p, WebsitePageSnapshot)
    }
    out = []
    for q in sorted(data.search.queries, key=lambda x: x.query_fingerprint):
        page = pages.get(q.matched_page_fingerprint or "")
        if q.current.impressions < 100 or not (Decimal(4) <= q.current.average_position <= 20):
            reason = EvaluationReason.threshold_not_met
        elif (
            q.current.average_position <= 10
            and q.current.ctr < Decimal(".02")
            and int(q.current.impressions * Decimal(".02")) - q.current.clicks >= 2
        ):
            reason = EvaluationReason.overlapping_detector_owner
        elif page is None:
            reason = EvaluationReason.target_page_missing
        elif page.indexable is not True:
            reason = EvaluationReason.target_page_nonindexable
        else:
            scores = search_scores(
                q.current,
                ActionFamily.existing_page_optimization,
                relevance=q.profile_relevance,
                signal=max(Decimal(0), Decimal(25) - q.current.average_position),
            )
            out.append(
                candidate(
                    data,
                    DETECTOR_ID,
                    OpportunityType.ranking_within_reach,
                    ActionFamily.existing_page_optimization,
                    "query",
                    q.query_fingerprint,
                    f"Improve ranking for {q.query_text}",
                    "Optimize the matched page.",
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
            )
            continue
        out.append(rejected(DETECTOR_ID, reason, q.query_fingerprint))
    return tuple(out) or (rejected(DETECTOR_ID, EvaluationReason.insufficient_evidence, "none"),)
