import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.modules.achievement_digests.enums import (
    AchievementDigestFeedbackAction,
    AchievementDigestFrequency,
)
from app.shared.schemas import ApiSchema


class AchievementDigestResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID | None
    frequency: AchievementDigestFrequency
    period_start: datetime
    period_end: datetime
    subject: str
    body: str
    digest_json: dict[str, Any]
    quality_score: int | None
    should_send: bool
    recommended_post: str | None
    event_ids: list[UUID]
    draft_ids: list[UUID]
    repo_ids: list[UUID]
    sent_at: datetime | None
    created_at: datetime


class AchievementDigestListResponse(ApiSchema):
    data: list[AchievementDigestResponse]


class AchievementDigestFeedbackCreate(ApiSchema):
    action: AchievementDigestFeedbackAction
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, separators=(",", ":"))) > 2000:
            raise ValueError("Metadata is too large.")
        return value


class AchievementDigestFeedbackResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    digest_id: UUID
    action: AchievementDigestFeedbackAction
    metadata: dict[str, Any]
    created_at: datetime


__all__ = [
    "AchievementDigestFeedbackAction",
    "AchievementDigestFeedbackCreate",
    "AchievementDigestFeedbackResponse",
    "AchievementDigestFrequency",
    "AchievementDigestListResponse",
    "AchievementDigestResponse",
]
