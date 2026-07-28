from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.operator.enums import FeedbackReason


class OpportunityFeedbackCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    opportunity_id: UUID
    evaluation_id: UUID | None = None
    reason_code: FeedbackReason
    usefulness: Annotated[int | None, Field(ge=1, le=5)] = None
    comment: Annotated[str | None, Field(max_length=500)] = None
    evaluation_eligible: bool = True
