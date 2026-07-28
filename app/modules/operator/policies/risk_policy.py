from typing import Literal

RiskOutcome = Literal["pass", "review", "block"]


def final_risk_outcome(deterministic: str, ai: str) -> RiskOutcome:
    if deterministic not in {"pass", "review", "block"} or ai not in {"pass", "review", "block"}:
        raise ValueError("invalid_risk_outcome")
    if deterministic == "block" or ai == "block":
        return "block"
    if deterministic == "review" or ai == "review":
        return "review"
    return "pass"
