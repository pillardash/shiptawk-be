from pydantic import BaseModel, ConfigDict


class CommitEnrichment(BaseModel):
    model_config = ConfigDict(frozen=True)

    ai_summary: str | None
    ai_user_benefit: str | None
