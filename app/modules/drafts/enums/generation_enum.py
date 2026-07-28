from typing import Literal

AngleType = Literal[
    "user_benefit",
    "problem_solved",
    "shipping_update",
    "technical_credibility",
    "founder_build_in_public",
    "milestone_or_traction",
]
MemoryAngleType = Literal[
    "user_benefit",
    "problem_solved",
    "shipping_update",
    "technical_credibility",
    "founder_build_in_public",
    "milestone_or_traction",
    "weekly_roundup",
]
CandidateVariant = Literal[
    "problem_solution",
    "customer_value",
    "founder_insight",
    "soft_launch_cta",
    "concise_shipping_update",
]
PipelineOutcome = Literal["generate", "hold", "ignore", "block"]
RejectionReason = Literal[
    "missing_evidence",
    "unknown_evidence",
    "unsupported_claim",
    "unsupported_metric",
    "unsupported_absolute",
    "wrong_product",
]
Tone = Literal["casual", "technical", "hype"]
PrivateRepoMode = Literal["normal", "conservative"]

__all__ = [
    "AngleType",
    "CandidateVariant",
    "MemoryAngleType",
    "PipelineOutcome",
    "PrivateRepoMode",
    "RejectionReason",
    "Tone",
]
