import json
from collections.abc import Sequence
from typing import Literal

RiskOutcome = Literal["pass", "review", "block"]


def evaluate_asset_safety(
    structured_content: dict[str, object],
    blocked_terms: Sequence[str],
    blocked_topics: Sequence[str],
    safe_public_boundaries: Sequence[str],
) -> RiskOutcome:
    content = json.dumps(structured_content, sort_keys=True).casefold()

    def contains(values: Sequence[str]) -> bool:
        return any(value.strip().casefold() in content for value in values if value.strip())

    if contains(blocked_terms) or contains(blocked_topics):
        return "block"
    if contains(safe_public_boundaries):
        return "review"
    return "pass"


def final_risk_outcome(deterministic: str, ai: str) -> RiskOutcome:
    if deterministic not in {"pass", "review", "block"} or ai not in {"pass", "review", "block"}:
        raise ValueError("invalid_risk_outcome")
    if deterministic == "block" or ai == "block":
        return "block"
    if deterministic == "review" or ai == "review":
        return "review"
    return "pass"
