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

DETECTOR_ID = "existing_page_needs_improvement"
DETECTOR_VERSION = "2"


def detect(data: DetectionInput) -> tuple[DetectorEvaluation, ...]:
    reasons = quality_reasons(data, requires_search=True, requires_website=True)
    if reasons:
        return (stopped(DETECTOR_ID, reasons),)
    assert data.search and data.website
    pages = {
        p.page_fingerprint: p for p in data.website.pages if isinstance(p, WebsitePageSnapshot)
    }
    conversion_destinations = {
        page.url.rstrip("/")
        for page in pages.values()
        if page.page_type == "conversion" and page.crawl_status == "succeeded"
    }
    if data.profile.conversion_url:
        conversion_destinations = {data.profile.conversion_url.rstrip("/")}
    out = []
    for sp in sorted(data.search.pages, key=lambda x: x.url_fingerprint):
        page = pages.get(sp.website_page_fingerprint or "")
        if sp.current.impressions < 50:
            reason = EvaluationReason.threshold_not_met
        elif sp.match_status != "matched" or page is None:
            reason = EvaluationReason.target_page_missing
        elif page.crawl_status != "succeeded":
            reason = EvaluationReason.website_crawl_failed
        else:
            conversion_path_defect = (
                page.internal_links is not None
                and bool(conversion_destinations)
                and (
                    page.url.rstrip("/") not in conversion_destinations
                    and conversion_destinations.isdisjoint(
                        link.rstrip("/") for link in page.internal_links
                    )
                )
            )
            defect_count = sum(
                (
                    not page.title,
                    not page.meta_description,
                    len(page.h1s) != 1,
                    page.indexable is False,
                    (
                        page.canonical_fingerprint is not None
                        and page.canonical_fingerprint != page.page_fingerprint
                    ),
                    conversion_path_defect,
                )
            )
            if defect_count:
                scores = search_scores(
                    sp.current,
                    ActionFamily.page_quality_fix,
                    signal=Decimal(defect_count * 8),
                )
                metadata_only = (
                    (not page.title or not page.meta_description)
                    and len(page.h1s) == 1
                    and page.indexable is not False
                    and (
                        page.canonical_fingerprint is None
                        or page.canonical_fingerprint == page.page_fingerprint
                    )
                    and not conversion_path_defect
                )
                out.append(
                    candidate(
                        data,
                        DETECTOR_ID,
                        OpportunityType.existing_page_needs_improvement,
                        ActionFamily.page_quality_fix,
                        "page",
                        page.page_fingerprint,
                        f"Fix page quality issues on {page.url}",
                        "Correct deterministic metadata, heading, indexing, canonical, "
                        "or conversion-path defects.",
                        sp.current,
                        data.as_of,
                        impact=scores["impact"],
                        confidence=scores["confidence"],
                        fit=scores["fit"],
                        urgency=scores["urgency"],
                        effort=Decimal(20) if metadata_only else scores["effort"],
                        source_id=data.search.source_id,
                        source_run_id=data.search.sync_id,
                    )
                )
                continue
            reason = EvaluationReason.no_page_quality_defect
        out.append(rejected(DETECTOR_ID, reason, sp.url_fingerprint))
    return tuple(out) or (rejected(DETECTOR_ID, EvaluationReason.insufficient_evidence, "none"),)
