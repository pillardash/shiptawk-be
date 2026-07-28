from decimal import Decimal

from app.modules.operator.detectors.opportunity_support_detector import candidate, rejected, stopped
from app.modules.operator.enums import ActionFamily, EvaluationReason, OpportunityType
from app.modules.operator.policies.data_quality_policy import (
    quality_reasons,
    shipping_signal_reasons,
)
from app.modules.operator.policies.detector_score_policy import shipping_scores
from app.modules.operator.schemas.opportunity_detection_schema import (
    DetectionInput,
    DetectorEvaluation,
    EvidenceSnapshot,
    MetricSnapshot,
)

DETECTOR_ID = "shipped_but_not_marketed"
DETECTOR_VERSION = "1"


def detect(data: DetectionInput) -> tuple[DetectorEvaluation, ...]:
    reasons = quality_reasons(data, requires_website=True)
    if reasons:
        return (stopped(DETECTOR_ID, reasons),)
    assert data.website
    out = []
    metrics = MetricSnapshot(clicks=0, impressions=0, ctr=Decimal(0), average_position=Decimal(0))
    for event in sorted(data.shipping.events, key=lambda x: x.event_key):
        event_reasons = shipping_signal_reasons(event, data.as_of)
        mappings = [
            m
            for m in data.website.capability_mappings
            if m.capability_key == event.capability_key
            and m.profile_version == data.profile.profile_version
        ]
        if event_reasons:
            out.append(rejected(DETECTOR_ID, event_reasons[0], event.event_key))
            continue
        if data.website.completed_at is None or data.website.completed_at <= event.occurred_at:
            out.append(rejected(DETECTOR_ID, EvaluationReason.website_stale, event.event_key))
            continue
        qualifying = [
            mapping
            for mapping in mappings
            if mapping.coverage_score >= Decimal(".60")
            and mapping.relevance_score >= Decimal(".20")
        ]
        status = (
            "missing"
            if not qualifying
            else min(
                qualifying,
                key=lambda mapping: (
                    {"missing": 0, "partial": 1, "complete": 2}[mapping.status],
                    mapping.page_fingerprint,
                ),
            ).status
        )
        if status == "complete":
            out.append(
                rejected(DETECTOR_ID, EvaluationReason.capability_mapping_current, event.event_key)
            )
            continue
        family = (
            ActionFamily.product_update
            if status == "missing"
            else ActionFamily.existing_page_optimization
        )
        evidence = (
            EvidenceSnapshot(
                evidence_key=f"shipping:{event.event_key}",
                source_type="shipping",
                source_record_type="shipping_event",
                source_id=event.repository_id,
                source_run_id=event.mapping_id,
                observed_at=event.occurred_at,
                metrics={"public_worthiness_score": event.public_worthiness_score},
                public_safe_summary=event.safe_summary or event.summary or "A capability shipped.",
                confidence_score=Decimal("80"),
                freshness_status="fresh",
            ),
            EvidenceSnapshot(
                evidence_key=f"website:{event.event_key}",
                source_type="website",
                source_record_type="capability_mapping",
                source_id=data.website.source_id,
                source_run_id=data.website.crawl_id,
                observed_at=data.website.completed_at,
                metrics={"mapping_count": Decimal(len(qualifying))},
                public_safe_summary=f"Website capability coverage is {status}.",
                confidence_score=Decimal("80"),
                freshness_status="fresh",
            ),
        )
        scores = shipping_scores(
            event.public_worthiness_score, event.capability_coverage_score, family
        )
        out.append(
            candidate(
                data,
                DETECTOR_ID,
                OpportunityType.shipped_but_not_marketed,
                family,
                "shipping_event",
                event.event_key,
                f"Market {event.safe_summary or event.summary or 'a shipped capability'}",
                "Prepare a safe product update."
                if status == "missing"
                else "Update the mapped product page.",
                metrics,
                event.occurred_at,
                impact=scores["impact"],
                confidence=scores["confidence"],
                fit=scores["fit"],
                urgency=scores["urgency"],
                effort=scores["effort"],
                evidence=evidence,
            )
        )
    return tuple(out) or (rejected(DETECTOR_ID, EvaluationReason.insufficient_evidence, "none"),)
