import json
from decimal import Decimal
from pathlib import Path

from app.modules.operator.enums import EvaluationOutcome
from app.modules.operator.policies.near_duplicate_policy import (
    NEAR_DUPLICATE_JACCARD_THRESHOLD,
    collapse_near_duplicates,
    token_jaccard,
)
from app.modules.operator.schemas import DetectionInput, DetectorEvaluation
from app.modules.operator.services.opportunity_detection_service import evaluate_detection_input

DATASET = Path(__file__).parent / "evaluations/opportunity_engine/v1/dataset.json"


def _accepted() -> DetectorEvaluation:
    data = DetectionInput.model_validate(json.loads(DATASET.read_text())["input"])
    return next(
        item
        for item in evaluate_detection_input(data)
        if item.outcome == EvaluationOutcome.accepted and item.candidate is not None
    )


def _evaluation(title: str, identity: str, priority: str) -> DetectorEvaluation:
    source = _accepted()
    assert source.candidate is not None
    scores = source.candidate.scores.model_copy(update={"priority": Decimal(priority)})
    candidate = source.candidate.model_copy(
        update={"title": title, "identity_fingerprint": identity, "scores": scores}
    )
    return source.model_copy(update={"candidate": candidate})


def test_threshold_is_inclusive_and_below_threshold_is_distinct() -> None:
    assert token_jaccard("one two three four", "one two three five") == Decimal("0.6")
    assert Decimal("0.60") == NEAR_DUPLICATE_JACCARD_THRESHOLD
    collapsed = collapse_near_duplicates(
        (_evaluation("one two three four", "a", "50"), _evaluation("one two three five", "b", "50"))
    )
    assert [item.outcome for item in collapsed].count(EvaluationOutcome.accepted) == 1
    distinct = collapse_near_duplicates(
        (_evaluation("one two three", "a", "50"), _evaluation("one two four", "b", "50"))
    )
    assert [item.outcome for item in distinct].count(EvaluationOutcome.accepted) == 2


def test_priority_then_fingerprints_choose_a_stable_winner_for_every_permutation() -> None:
    low = _evaluation("create content for analytics reports", "z", "40")
    high_z = _evaluation("create content for analytics reporting", "z2", "80")
    high_a = _evaluation("create content for analytics report", "a", "80")
    expected = "a"
    for values in ((low, high_z, high_a), (high_a, low, high_z), (high_z, high_a, low)):
        accepted = [
            item
            for item in collapse_near_duplicates(values)
            if item.outcome == EvaluationOutcome.accepted
        ]
        assert len(accepted) == 1
        assert accepted[0].candidate is not None
        assert accepted[0].candidate.identity_fingerprint == expected
