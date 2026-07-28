from datetime import UTC, datetime, time
from decimal import Decimal

from app.modules.operator.enums import (
    ActionFamily,
    EffortBand,
    EvaluationOutcome,
    EvaluationReason,
    OpportunityType,
)
from app.modules.operator.policies.identity_policy import (
    identity_fingerprint,
    observation_fingerprint,
)
from app.modules.operator.policies.scoring_policy import score_gate, score_opportunity
from app.modules.operator.schemas.opportunity_detection_schema import (
    DetectionInput,
    DetectorCandidate,
    DetectorEvaluation,
    EvidenceSnapshot,
    MetricSnapshot,
)


def stopped(
    detector_id: str,
    reasons: tuple[EvaluationReason, ...],
    diagnostics: dict[str, object] | None = None,
) -> DetectorEvaluation:
    return DetectorEvaluation(
        detector_id=detector_id,
        detector_version="1",
        outcome=EvaluationOutcome.stopped,
        reason_codes=reasons,
        diagnostics=diagnostics or {},
    )


def rejected(detector_id: str, reason: EvaluationReason, key: str) -> DetectorEvaluation:
    return DetectorEvaluation(
        detector_id=detector_id,
        detector_version="1",
        outcome=EvaluationOutcome.no_recommendation,
        reason_codes=(reason,),
        diagnostics={"subject": key},
    )


def candidate(
    data: DetectionInput,
    detector_id: str,
    opportunity_type: OpportunityType,
    action_family: ActionFamily,
    subject_kind: str,
    key: str,
    title: str,
    action: str,
    metrics: MetricSnapshot,
    observed_at: datetime,
    *,
    confidence: Decimal = Decimal(80),
    fit: Decimal = Decimal(75),
    impact: Decimal = Decimal(70),
    urgency: Decimal = Decimal(60),
    effort: Decimal = Decimal(30),
    expected_metric: dict[str, object] | None = None,
    evidence: tuple[EvidenceSnapshot, ...] | None = None,
    source_type: str = "search",
    source_id: object = None,
    source_run_id: object = None,
    effort_band: EffortBand = EffortBand.small,
) -> DetectorEvaluation:
    keys = {subject_kind: key}
    identity = identity_fingerprint(
        data.product_id, opportunity_type, subject_kind, keys, action_family
    )
    scores = score_opportunity(
        impact=impact,
        confidence=confidence,
        effort=effort,
        urgency=urgency,
        strategic_fit=fit,
        novelty=Decimal(100),
    )
    gates = score_gate(scores)
    if source_type == "search" and data.search and data.search.current_end:
        observed_at = datetime.combine(data.search.current_end, time.min, tzinfo=UTC)
    search_source_id = data.search.source_id if data.search and source_type == "search" else None
    search_run_id = data.search.sync_id if data.search and source_type == "search" else None
    period = {}
    if data.search:
        period = {
            "current_start": (
                data.search.current_start.isoformat() if data.search.current_start else None
            ),
            "current_end": data.search.current_end.isoformat() if data.search.current_end else None,
            "prior_start": data.search.prior_start.isoformat() if data.search.prior_start else None,
            "prior_end": data.search.prior_end.isoformat() if data.search.prior_end else None,
        }
    default_evidence = EvidenceSnapshot(
        evidence_key=f"{detector_id}:{key}",
        source_type=source_type,
        source_record_type=subject_kind,
        source_id=source_id or search_source_id,
        source_run_id=source_run_id or search_run_id,
        observed_at=observed_at,
        period=period,
        metrics={
            "clicks": Decimal(metrics.clicks),
            "impressions": Decimal(metrics.impressions),
            "ctr": metrics.ctr,
            "average_position": metrics.average_position,
        },
        public_safe_summary=title,
        confidence_score=confidence,
        freshness_status="fresh",
    )
    evidence_items = evidence or (default_evidence,)
    if (
        evidence is None
        and data.website
        and detector_id in {"ranking_within_reach", "existing_page_needs_improvement"}
        and data.website.completed_at
    ):
        evidence_items += (
            EvidenceSnapshot(
                evidence_key=f"{detector_id}:website:{key}",
                source_type="website",
                source_record_type="website_page",
                source_id=data.website.source_id,
                source_run_id=data.website.crawl_id,
                observed_at=data.website.completed_at,
                metrics={},
                public_safe_summary="The latest successful crawl verified the target page.",
                confidence_score=confidence,
                freshness_status="fresh",
            ),
        )
    observation = observation_fingerprint(
        detector_id=detector_id,
        detector_version="1",
        policy_versions={"scoring": "v1", "quality": "v1"},
        periods=data.search.model_dump() if data.search else {},
        evidence=[item.model_dump() for item in evidence_items],
        metrics=metrics.model_dump(),
        profile_id=data.profile.profile_id,
        source_ids=(
            data.search.source_id if data.search else None,
            data.website.source_id if data.website else None,
        ),
    )
    item = DetectorCandidate(
        opportunity_type=opportunity_type,
        action_family=action_family,
        subject_kind=subject_kind,
        subject_keys=keys,
        identity_fingerprint=identity,
        observation_fingerprint=observation,
        title=title,
        interpretation=title,
        recommended_action=action,
        expected_metric=expected_metric or {"name": "clicks", "direction": "increase"},
        effort_band=effort_band,
        evidence=evidence_items,
        scores=scores,
    )
    return DetectorEvaluation(
        detector_id=detector_id,
        detector_version="1",
        outcome=EvaluationOutcome.no_recommendation if gates else EvaluationOutcome.accepted,
        reason_codes=gates,
        candidate=None if gates else item,
    )
