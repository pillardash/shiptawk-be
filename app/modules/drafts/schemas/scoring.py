from pydantic import BaseModel, ConfigDict

from app.modules.drafts.enums.generation_enum import PipelineOutcome


class ScoringModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ScoreDimensions(ScoringModel):
    user_impact: int
    novelty: int
    clarity: int
    safety: int
    proof: int


class PublicWorthinessResult(ScoringModel):
    score: int
    dimensions: ScoreDimensions
    outcome: PipelineOutcome
    rationale: str
    is_public_worthy: bool
    failed_safety_checks: list[str]
