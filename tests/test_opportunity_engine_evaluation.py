import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.modules.operator.enums import EvaluationOutcome, OpportunityStatus
from app.modules.operator.schemas import DetectionInput, OpportunityHistoryItem
from app.modules.operator.services.opportunity_detection_service import evaluate_detection_input
from app.modules.operator.services.opportunity_snapshot_service import canonicalize_detection_input

DATASET = Path(__file__).parent / "evaluations/opportunity_engine/v1/dataset.json"
DETECTORS = {
    "high_impressions_low_ctr",
    "ranking_within_reach",
    "search_decline",
    "shipped_but_not_marketed",
    "search_demand_without_dedicated_content",
    "existing_page_needs_improvement",
}


def _load() -> tuple[dict[str, Any], DetectionInput]:
    raw = json.loads(DATASET.read_text())
    assert raw["schemaVersion"] == "opportunity-engine-eval.v1"
    assert raw["contractVersions"] == {
        "detectionInput": "1",
        "detectorSuite": "v1",
        "qualityPolicy": "v1",
        "scoringPolicy": "v1",
        "cooldownPolicy": "v1",
    }
    return raw, DetectionInput.model_validate(raw["input"])


def _query(data: DetectionInput, key: str, **updates: object) -> DetectionInput:
    assert data.search is not None
    queries = tuple(
        item.model_copy(update=updates) if item.query_fingerprint == key else item
        for item in data.search.queries
    )
    return data.model_copy(update={"search": data.search.model_copy(update={"queries": queries})})


@pytest.mark.evaluation
def test_six_one_unit_below_threshold_controls() -> None:
    _, data = _load()
    assert data.search is not None and data.website is not None
    originals = {
        item.detector_id: item.candidate.identity_fingerprint
        for item in evaluate_detection_input(data)
        if item.outcome == EvaluationOutcome.accepted and item.candidate is not None
    }
    ctr = next(item for item in data.search.queries if item.query_fingerprint == "q-ctr")
    rank = next(item for item in data.search.queries if item.query_fingerprint == "q-rank")
    decline = next(item for item in data.search.queries if item.query_fingerprint == "q-decline")
    demand = next(item for item in data.search.queries if item.query_fingerprint == "q-demand")
    event = data.shipping.events[0]
    search_page = data.search.pages[0]
    cases = {
        "high_impressions_low_ctr": _query(
            data, "q-ctr", current=ctr.current.model_copy(update={"impressions": 99})
        ),
        "ranking_within_reach": _query(
            data,
            "q-rank",
            current=rank.current.model_copy(update={"average_position": Decimal("21")}),
        ),
        "search_decline": _query(
            data,
            "q-decline",
            prior=decline.prior.model_copy(update={"clicks": 12, "impressions": 249}),
        ),
        "search_demand_without_dedicated_content": _query(
            data, "q-demand", current=demand.current.model_copy(update={"impressions": 49})
        ),
        "shipped_but_not_marketed": data.model_copy(
            update={
                "shipping": data.shipping.model_copy(
                    update={
                        "events": (
                            event.model_copy(update={"public_worthiness_score": Decimal("65")}),
                        )
                    }
                )
            }
        ),
        "existing_page_needs_improvement": data.model_copy(
            update={
                "search": data.search.model_copy(
                    update={
                        "pages": (
                            search_page.model_copy(
                                update={
                                    "current": search_page.current.model_copy(
                                        update={"impressions": 49}
                                    )
                                }
                            ),
                        )
                    }
                )
            }
        ),
    }
    misses = 0
    for detector_id, case in cases.items():
        accepted_identities = {
            item.candidate.identity_fingerprint
            for item in evaluate_detection_input(case)
            if item.outcome == EvaluationOutcome.accepted and item.candidate is not None
        }
        misses += originals[detector_id] not in accepted_identities
    assert len(cases) == 6 and misses == 6, f"below-threshold controls={misses}/{len(cases)}"


@pytest.mark.evaluation
def test_versioned_opportunity_engine_release_gates() -> None:
    dataset, data = _load()
    assert data.search is not None and data.website is not None
    first = evaluate_detection_input(data)
    second = evaluate_detection_input(data)
    accepted = [item for item in first if item.outcome == EvaluationOutcome.accepted]
    accepted_by_detector = {item.detector_id: item for item in accepted}
    assert accepted_by_detector.keys() >= DETECTORS, (
        f"positive detector coverage={len(accepted_by_detector)}/6; "
        f"missing={sorted(DETECTORS - accepted_by_detector.keys())}"
    )

    permuted = data.model_copy(
        update={
            "search": data.search.model_copy(
                update={
                    "queries": tuple(reversed(data.search.queries)),
                    "pages": tuple(reversed(data.search.pages)),
                }
            ),
            "website": data.website.model_copy(
                update={"pages": tuple(reversed(data.website.pages))}
            ),
            "shipping": data.shipping.model_copy(
                update={"events": tuple(reversed(data.shipping.events))}
            ),
        }
    )
    reproducible = int(first == second == evaluate_detection_input(permuted))
    assert (
        canonicalize_detection_input(data).input_hash
        == canonicalize_detection_input(permuted).input_hash
    )

    cooldown_cases = 0
    cooldown_leaks = 0
    for detector_id in sorted(DETECTORS):
        candidate = accepted_by_detector[detector_id].candidate
        assert candidate is not None
        history = OpportunityHistoryItem(
            identity_fingerprint=candidate.identity_fingerprint,
            action_family=candidate.action_family,
            opportunity_type=candidate.opportunity_type,
            status=OpportunityStatus.open,
            occurred_at=data.as_of - timedelta(days=1),
            cooldown_until=data.as_of + timedelta(days=30),
        )
        replay = evaluate_detection_input(
            data.model_copy(
                update={"history": data.history.model_copy(update={"opportunities": (history,)})}
            )
        )
        matching = [item for item in replay if item.detector_id == detector_id and item.candidate]
        cooldown_cases += 1
        cooldown_leaks += sum(item.outcome == EvaluationOutcome.accepted for item in matching)

    insufficient_cases = [
        data.model_copy(update={"profile": data.profile.model_copy(update={"completeness": 99})}),
        data.model_copy(
            update={
                "profile": data.profile.model_copy(update={"completeness": 98}),
                "search": data.search.model_copy(update={"truncated": True}),
            }
        ),
        data.model_copy(
            update={
                "profile": data.profile.model_copy(update={"completeness": 97}),
                "search": None,
            }
        ),
        data.model_copy(
            update={
                "profile": data.profile.model_copy(update={"completeness": 96}),
                "website": None,
            }
        ),
        data.model_copy(
            update={
                "profile": data.profile.model_copy(update={"completeness": 95}),
                "website": data.website.model_copy(
                    update={"completed_at": data.as_of - timedelta(days=8)}
                ),
            }
        ),
        data.model_copy(
            update={
                "profile": data.profile.model_copy(update={"completeness": 94}),
                "search": data.search.model_copy(update={"complete": False}),
            }
        ),
    ]
    insufficient_leaks = sum(
        item.outcome == EvaluationOutcome.accepted
        for case in insufficient_cases
        for item in evaluate_detection_input(case)
    )

    reviewed = dataset["reviewedNearDuplicatePairs"]
    duplicate_reviews = [pair for pair in reviewed if pair["label"] == "duplicate"]
    negative_controls = [pair for pair in reviewed if pair["label"] == "distinct"]
    demand = next(item for item in data.search.queries if item.query_fingerprint == "q-demand")
    near_duplicate_leaks = 0
    false_collapses = 0
    for pair in reviewed:
        queries = tuple(
            demand.model_copy(
                update={
                    "query_text": side["queryText"],
                    "query_fingerprint": side["queryFingerprint"],
                }
            )
            for side in (pair["left"], pair["right"])
        )
        pair_input = data.model_copy(
            update={
                "search": data.search.model_copy(update={"queries": queries, "pages": ()}),
                "shipping": data.shipping.model_copy(update={"events": ()}),
            }
        )
        accepted_count = sum(
            item.outcome == EvaluationOutcome.accepted
            for item in evaluate_detection_input(pair_input)
            if item.detector_id == "search_demand_without_dedicated_content"
        )
        if pair["label"] == "duplicate":
            near_duplicate_leaks += accepted_count > 1
        else:
            false_collapses += accepted_count < 2

    evidence_total = sum(len(item.candidate.evidence) for item in accepted if item.candidate)
    evidence_correct = sum(
        bool(reference.evidence_key and reference.observed_at <= data.as_of)
        for item in accepted
        if item.candidate
        for reference in item.candidate.evidence
    )
    metrics = {
        "reproducibility": f"{reproducible}/1",
        "cooldownDuplicateLeaks": f"{cooldown_leaks}/{cooldown_cases}",
        "reviewedNearDuplicateLeaks": f"{near_duplicate_leaks}/{len(duplicate_reviews)}",
        "reviewedDistinctFalseCollapses": f"{false_collapses}/{len(negative_controls)}",
        "insufficientEvidenceLeaks": f"{insufficient_leaks}/{len(insufficient_cases)}",
        "evidenceReferenceCorrectness": f"{evidence_correct}/{evidence_total}",
    }
    message = f"release metrics: {metrics}"
    assert reproducible == 1, message
    assert cooldown_cases >= 6 and cooldown_leaks == 0, message
    assert len(reviewed) >= 20, message
    assert len(duplicate_reviews) >= 10 and len(negative_controls) >= 10, message
    assert near_duplicate_leaks / len(duplicate_reviews) < 0.05, message
    assert false_collapses == 0, message
    assert len(insufficient_cases) >= 6 and insufficient_leaks == 0, message
    assert evidence_total > 0 and evidence_correct == evidence_total, message
    print(message)
