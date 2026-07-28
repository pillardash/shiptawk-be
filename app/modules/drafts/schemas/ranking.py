from pydantic import BaseModel, ConfigDict


class RankingModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class RankingDimensions(RankingModel):
    user_impact: float
    novelty: float
    clarity: float
    safety: float
    proof: float


class TweetOption(RankingModel):
    content: str
    rank: int
    score: float
    rationale: str
    angle: str
    variant: str
    evidence_ids: list[str]
    dimension_scores: RankingDimensions
