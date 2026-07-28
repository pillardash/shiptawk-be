import re
from collections.abc import Iterable
from decimal import Decimal

from app.modules.operator.enums import EvaluationOutcome, EvaluationReason
from app.modules.operator.schemas import DetectorEvaluation

NEAR_DUPLICATE_POLICY_VERSION = "v1"
NEAR_DUPLICATE_JACCARD_THRESHOLD = Decimal("0.60")
_TOKEN = re.compile(r"[a-z0-9]+")


def normalized_tokens(value: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(value.casefold()))


def token_jaccard(left: str, right: str) -> Decimal:
    left_tokens, right_tokens = normalized_tokens(left), normalized_tokens(right)
    union = left_tokens | right_tokens
    if not union:
        return Decimal("1")
    return Decimal(len(left_tokens & right_tokens)) / Decimal(len(union))


def collapse_near_duplicates(
    evaluations: Iterable[DetectorEvaluation],
) -> tuple[DetectorEvaluation, ...]:
    ordered = sorted(
        evaluations,
        key=lambda item: (
            -(item.candidate.scores.priority if item.candidate else Decimal("-1")),
            item.candidate.identity_fingerprint if item.candidate else "",
            item.candidate.observation_fingerprint if item.candidate else "",
            item.detector_id,
        ),
    )
    winners: list[DetectorEvaluation] = []
    result: list[DetectorEvaluation] = []
    for item in ordered:
        candidate = item.candidate
        if item.outcome != EvaluationOutcome.accepted or candidate is None:
            result.append(item)
            continue
        duplicate = any(
            winner.candidate is not None
            and winner.candidate.opportunity_type == candidate.opportunity_type
            and winner.candidate.action_family == candidate.action_family
            and token_jaccard(winner.candidate.title, candidate.title)
            >= NEAR_DUPLICATE_JACCARD_THRESHOLD
            for winner in winners
        )
        if duplicate:
            result.append(
                item.model_copy(
                    update={
                        "outcome": EvaluationOutcome.superseded_in_run,
                        "reason_codes": tuple(
                            dict.fromkeys((*item.reason_codes, EvaluationReason.lower_priority))
                        ),
                    }
                )
            )
        else:
            winners.append(item)
            result.append(item)
    return tuple(result)
