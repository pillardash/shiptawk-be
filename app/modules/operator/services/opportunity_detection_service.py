import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.detectors.opportunity_registry_detector import DETECTOR_REGISTRY
from app.modules.operator.enums import EvaluationOutcome, EvaluationReason, OpportunityStatus
from app.modules.operator.models import (
    OperatorRunSnapshot,
    Opportunity,
    OpportunityEvaluation,
    OpportunityEvidence,
)
from app.modules.operator.policies.cooldown_policy import COMPLETED_DAYS, novelty_for_history
from app.modules.operator.policies.near_duplicate_policy import collapse_near_duplicates
from app.modules.operator.policies.ranking_policy import rank_and_collapse
from app.modules.operator.policies.scoring_policy import (
    SCORING_VERSION,
    confidence_label,
    score_opportunity,
)
from app.modules.operator.repositories import (
    operator_run_repository,
    opportunity_evaluation_repository,
    opportunity_repository,
)
from app.modules.operator.schemas import (
    DetectionInput,
    DetectorCandidate,
    DetectorEvaluation,
)
from app.modules.operator.services.opportunity_snapshot_service import (
    DetectionInputAssembly,
    SnapshotHashMismatchError,
    canonicalize_detection_input,
)


class DetectionPersistenceConflictError(ValueError):
    pass


class OperatorRunNotFoundError(LookupError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _evaluation_key(evaluation: DetectorEvaluation, ordinal: int) -> str:
    payload = evaluation.model_dump(mode="json", exclude_none=False)
    return hashlib.sha256(f"{ordinal}:".encode() + _canonical(payload).encode()).hexdigest()


def _rescore(candidate: DetectorCandidate, novelty: Decimal) -> DetectorCandidate:
    scores = candidate.scores
    return candidate.model_copy(
        update={
            "scores": score_opportunity(
                impact=scores.impact,
                confidence=scores.confidence,
                effort=scores.effort,
                urgency=scores.urgency,
                strategic_fit=scores.strategic_fit,
                novelty=novelty,
            )
        }
    )


def summarize_evaluations(evaluations: list[OpportunityEvaluation]) -> dict[str, object]:
    outcomes = Counter(item.outcome for item in evaluations)
    reasons = Counter(reason for item in evaluations for reason in item.reason_codes)
    return {
        "accepted": outcomes[EvaluationOutcome.accepted.value],
        "no_recommendation": outcomes[EvaluationOutcome.no_recommendation.value],
        "stopped": outcomes[EvaluationOutcome.stopped.value],
        "suppressed": sum(
            outcomes[value]
            for value in (
                EvaluationOutcome.suppressed_duplicate.value,
                EvaluationOutcome.suppressed_cooldown.value,
                EvaluationOutcome.superseded_in_run.value,
            )
        ),
        "reason_frequencies": dict(sorted(reasons.items())),
    }


def _collapse_same_identity(
    evaluations: tuple[DetectorEvaluation, ...],
) -> tuple[DetectorEvaluation, ...]:
    seen: set[str] = set()
    result: list[DetectorEvaluation] = []
    for evaluation in evaluations:
        candidate = evaluation.candidate
        if evaluation.outcome != EvaluationOutcome.accepted or candidate is None:
            result.append(evaluation)
            continue
        if candidate.identity_fingerprint in seen:
            result.append(
                evaluation.model_copy(
                    update={
                        "outcome": EvaluationOutcome.superseded_in_run,
                        "reason_codes": (*evaluation.reason_codes, EvaluationReason.lower_priority),
                    }
                )
            )
            continue
        seen.add(candidate.identity_fingerprint)
        result.append(evaluation)
    return tuple(result)


def evaluate_detection_input(data: DetectionInput) -> tuple[DetectorEvaluation, ...]:
    """Evaluate an immutable snapshot without database, clock, or global mutation."""
    gathered = tuple(
        evaluation for _, _, detector in DETECTOR_REGISTRY for evaluation in detector(data)
    )
    collapsed = collapse_near_duplicates(_collapse_same_identity(rank_and_collapse(gathered)))
    result: list[DetectorEvaluation] = []
    for evaluation in collapsed:
        candidate = evaluation.candidate
        if evaluation.outcome != EvaluationOutcome.accepted or candidate is None:
            result.append(evaluation)
            continue
        history = tuple(
            item
            for item in data.history.opportunities
            if item.identity_fingerprint == candidate.identity_fingerprint
        )
        novelty, blocked = novelty_for_history(history, candidate.opportunity_type, data.as_of)
        candidate = _rescore(candidate, novelty)
        if blocked is None:
            result.append(evaluation.model_copy(update={"candidate": candidate}))
            continue
        outcome = (
            EvaluationOutcome.suppressed_duplicate
            if blocked == EvaluationReason.duplicate
            else EvaluationOutcome.suppressed_cooldown
        )
        result.append(
            evaluation.model_copy(
                update={
                    "candidate": candidate,
                    "outcome": outcome,
                    "reason_codes": tuple(dict.fromkeys((*evaluation.reason_codes, blocked))),
                }
            )
        )
    return tuple(result)


def evaluation_projection(
    evaluations: tuple[DetectorEvaluation, ...],
) -> tuple[dict[str, object], ...]:
    """Return the persistence-comparable, linkage-free evaluation representation."""
    return tuple(
        {
            "detector_id": item.detector_id,
            "detector_version": item.detector_version,
            "outcome": item.outcome.value,
            "reason_codes": [reason.value for reason in item.reason_codes],
            "identity_fingerprint": (
                item.candidate.identity_fingerprint if item.candidate is not None else None
            ),
            "observation_fingerprint": (
                item.candidate.observation_fingerprint if item.candidate is not None else None
            ),
            "candidate_snapshot": (
                item.candidate.model_dump(mode="json") if item.candidate is not None else None
            ),
            "priority_score": (
                str(item.candidate.scores.priority) if item.candidate is not None else None
            ),
            "evidence": (
                [evidence.model_dump(mode="json") for evidence in item.candidate.evidence]
                if item.candidate is not None
                else []
            ),
        }
        for _, item in sorted(
            (_evaluation_key(item, ordinal), item) for ordinal, item in enumerate(evaluations)
        )
    )


def replay_run_snapshot(snapshot: OperatorRunSnapshot) -> tuple[dict[str, object], ...]:
    try:
        detection_input = DetectionInput.model_validate(snapshot.input_payload)
        assembly = canonicalize_detection_input(detection_input)
    except Exception as exc:
        raise SnapshotHashMismatchError("Stored snapshot is not valid detection input.") from exc
    if assembly.input_hash != snapshot.input_hash or assembly.payload != snapshot.input_payload:
        raise SnapshotHashMismatchError("Stored snapshot payload does not match its SHA-256 hash.")
    return evaluation_projection(evaluate_detection_input(detection_input))


async def _persist_evidence(
    db: AsyncSession,
    evaluation: OpportunityEvaluation,
    candidate: DetectorCandidate,
    opportunity_id: UUID | None,
) -> None:
    for evidence in candidate.evidence:
        safe = evidence.model_dump(mode="json")
        source_payload = {
            key: safe[key]
            for key in (
                "evidence_key",
                "source_type",
                "source_record_type",
                "source_id",
                "source_run_id",
                "observed_at",
            )
        }
        await opportunity_evaluation_repository.add_evidence(
            db,
            OpportunityEvidence(
                workspace_id=evaluation.workspace_id,
                product_id=evaluation.product_id,
                evaluation_id=evaluation.id,
                opportunity_id=opportunity_id,
                evidence_key=evidence.evidence_key,
                source_type=evidence.source_type.value,
                source_record_type=evidence.source_record_type,
                source_id=evidence.source_id,
                source_run_id=evidence.source_run_id,
                observed_at=evidence.observed_at,
                period=safe["period"],
                metrics=safe["metrics"],
                public_safe_summary=evidence.public_safe_summary,
                confidence_score=evidence.confidence_score,
                freshness_status=evidence.freshness_status.value,
                quality_warnings=list(evidence.quality_warnings),
                source_fingerprint=hashlib.sha256(_canonical(source_payload).encode()).hexdigest(),
                schema_version="1",
            ),
        )


async def persist_detection(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    operator_run_id: UUID,
    assembly: DetectionInputAssembly,
    lease_owner: str | None = None,
) -> list[OpportunityEvaluation]:
    try:
        run = await operator_run_repository.get_product_run(
            db, workspace_id, product_id, operator_run_id, for_update=True
        )
        if run is None or run.run_kind != "opportunity_detection":
            raise OperatorRunNotFoundError(
                "Product-scoped opportunity detection run was not found."
            )
        if (
            assembly.detection_input.workspace_id != workspace_id
            or assembly.detection_input.product_id != product_id
        ):
            raise DetectionPersistenceConflictError(
                "Detection input tenant does not match the run."
            )
        existing = await opportunity_evaluation_repository.list_for_run(
            db, workspace_id, product_id, operator_run_id
        )
        if existing:
            if run.input_fingerprint != assembly.input_hash:
                raise DetectionPersistenceConflictError(
                    "Run already contains another detection input."
                )
            return existing

        raw = list(evaluate_detection_input(assembly.detection_input))

        persisted: list[OpportunityEvaluation] = []
        for index, source in enumerate(raw):
            candidate = source.candidate
            outcome = source.outcome
            reasons = list(source.reason_codes)
            opportunity: Opportunity | None = None
            if outcome == EvaluationOutcome.accepted and candidate is not None:
                await opportunity_repository.acquire_identity_lock(
                    db, workspace_id, product_id, candidate.identity_fingerprint
                )
                if outcome == EvaluationOutcome.accepted:
                    scores = candidate.scores
                    opportunity = Opportunity(
                        workspace_id=workspace_id,
                        product_id=product_id,
                        operator_run_id=run.id,
                        opportunity_type=candidate.opportunity_type.value,
                        action_family=candidate.action_family.value,
                        subject_kind=candidate.subject_kind,
                        subject_keys=candidate.subject_keys,
                        identity_fingerprint=candidate.identity_fingerprint,
                        observation_fingerprint=candidate.observation_fingerprint,
                        detector_id=source.detector_id,
                        detector_version=source.detector_version,
                        title=candidate.title,
                        evidence_ids=[],
                        interpretation=candidate.interpretation,
                        recommendation=candidate.recommended_action,
                        confidence=confidence_label(scores.confidence).value,
                        missing_information=[],
                        approval_level="review",
                        measurement_window="28_days",
                        stop_conditions=[],
                        impact_score=int(scores.impact),
                        effort_score=int(scores.effort),
                        urgency_score=int(scores.urgency),
                        confidence_score=scores.confidence,
                        strategic_fit_score=scores.strategic_fit,
                        novelty_score=scores.novelty,
                        priority_score=scores.priority,
                        score_version=SCORING_VERSION,
                        expected_metric=candidate.model_dump(mode="json")["expected_metric"],
                        effort_band=candidate.effort_band.value,
                        lifecycle_version="1",
                        status=OpportunityStatus.open.value,
                        cooldown_dedup_key=candidate.identity_fingerprint,
                        cooldown_until=assembly.detection_input.as_of
                        + timedelta(days=COMPLETED_DAYS[candidate.opportunity_type]),
                        provenance={"inputHash": assembly.input_hash, "evaluationOrdinal": index},
                        created_by_actor_id=run.created_by_actor_id,
                    )
                    await opportunity_repository.add(db, opportunity)

            candidate_json = candidate.model_dump(mode="json") if candidate is not None else None
            record = OpportunityEvaluation(
                workspace_id=workspace_id,
                product_id=product_id,
                operator_run_id=run.id,
                accepted_opportunity_id=opportunity.id if opportunity is not None else None,
                detector_id=source.detector_id,
                detector_version=source.detector_version,
                outcome=outcome.value,
                evaluation_key=_evaluation_key(source, index),
                reason_codes=[reason.value for reason in dict.fromkeys(reasons)],
                input_fingerprint=assembly.input_hash,
                identity_fingerprint=candidate.identity_fingerprint if candidate else None,
                observation_fingerprint=candidate.observation_fingerprint if candidate else None,
                candidate_snapshot=candidate_json,
                diagnostics=source.model_dump(mode="json")["diagnostics"],
                input_schema_version=assembly.detection_input.schema_version,
                detector_suite_version=run.detector_suite_version or "v1",
                quality_policy_version=run.quality_policy_version or "v1",
                scoring_policy_version=run.scoring_policy_version or SCORING_VERSION,
                cooldown_policy_version=run.cooldown_policy_version or "v1",
                score_version=SCORING_VERSION,
                priority_score=candidate.scores.priority if candidate else None,
            )
            await opportunity_evaluation_repository.add(db, record)
            if candidate is not None:
                await _persist_evidence(
                    db, record, candidate, opportunity.id if opportunity else None
                )
            persisted.append(record)

        run.input_fingerprint = assembly.input_hash
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.summary = summarize_evaluations(persisted)
        if lease_owner is not None:
            if run.lease_owner != lease_owner:
                raise DetectionPersistenceConflictError(
                    "Opportunity detection lease ownership was lost."
                )
            run.lease_owner = None
            run.lease_expires_at = None
        await operator_run_repository.flush(db, run)
        await db.commit()
        return sorted(persisted, key=lambda item: item.evaluation_key)
    except Exception:
        await db.rollback()
        raise
