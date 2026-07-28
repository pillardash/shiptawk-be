from pydantic import BaseModel, ConfigDict, Field

from app.modules.drafts.enums.generation_enum import MemoryAngleType


class MemoryModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class ContentMemory(MemoryModel):
    recent_approved_content: list[str] = Field(alias="recentApprovedContent")
    approved_angles: list[MemoryAngleType] = Field(alias="approvedAngles")


class RepetitionResult(MemoryModel):
    max_similarity: float
    repeated_angle_count: int
    penalty: int
    reasons: list[str]


class DraftMemoryInput(MemoryModel):
    content: str
    source_event_payload: dict[str, object]
