from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.drafts.enums.generation_enum import PipelineOutcome
from app.modules.drafts.schemas.context import EffectiveGenerationContext
from app.modules.drafts.schemas.memory import ContentMemory
from app.modules.drafts.schemas.ranking import TweetOption
from app.modules.drafts.schemas.summary import CommitEnrichment
from app.modules.integrations.events import NormalizedEvent


class ServiceModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class GenerationInput(ServiceModel):
    workspace_id: UUID
    user_id: UUID
    repo_id: UUID
    product_id: UUID | None
    normalized_event_id: UUID
    stable_key: str
    correlation_id: str
    event: NormalizedEvent
    context: EffectiveGenerationContext
    repo_private: bool
    drafts_created_this_week: int
    enrichment: CommitEnrichment | None = None
    content_memory: ContentMemory | None = None
    trigger: Literal["event_pipeline", "angle_regeneration"] = "event_pipeline"
    requested_angle: str | None = None


class GenerationExecutionResult(ServiceModel):
    outcome: PipelineOutcome | Literal["completed", "failed"]
    run_id: UUID | None
    reused: bool
    candidates: list[TweetOption]
