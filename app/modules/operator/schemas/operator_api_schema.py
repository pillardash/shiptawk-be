from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.modules.operator.enums import DismissalReason, FeedbackReason
from app.shared.schemas import ApiSchema


class OperatorRunRequestSchema(ApiSchema):
    model_config = ConfigDict(extra="forbid")
    trigger: Literal["manual"] = "manual"
    run_kind: Literal["opportunity_detection", "weekly_growth"] = "opportunity_detection"


class OperatorRunResponseSchema(ApiSchema):
    id: UUID
    trigger: str
    status: str
    run_kind: str
    as_of: datetime | None
    summary: dict[str, object]
    started_at: datetime
    completed_at: datetime | None
    current_stage: str | None = None
    stage_summary: dict[str, object] | None = None
    failure_category: str | None = None
    logical_period_start: datetime | None = None
    logical_period_end: datetime | None = None


class OperatorRunListSchema(ApiSchema):
    items: list[OperatorRunResponseSchema]
    limit: int
    offset: int


class OpportunityEvaluationResponseSchema(ApiSchema):
    id: UUID
    detector_id: str
    detector_version: str
    outcome: str
    reason_codes: list[str]
    priority_score: float | None
    accepted_opportunity_id: UUID | None


class OpportunityResponseSchema(ApiSchema):
    id: UUID
    opportunity_type: str | None
    action_family: str | None
    subject_kind: str | None
    subject_keys: dict[str, object] | None
    title: str
    interpretation: str
    recommendation: str
    impact_score: int
    effort_score: int
    urgency_score: int
    confidence_score: float | None
    strategic_fit_score: float | None
    novelty_score: float | None
    priority_score: float | None
    confidence: str
    expected_metric: dict[str, object] | None
    status: str
    created_at: datetime


class OpportunityListSchema(ApiSchema):
    items: list[OpportunityResponseSchema]
    limit: int
    offset: int


class OpportunityEvidenceResponseSchema(ApiSchema):
    id: UUID
    source_type: str
    source_record_type: str
    observed_at: datetime
    period: dict[str, object]
    metrics: dict[str, object]
    public_safe_summary: str
    confidence_score: float
    freshness_status: str
    quality_warnings: list[str]


class OpportunityFeedbackRequestSchema(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    reason: FeedbackReason
    usefulness: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)
    evaluation_eligible: bool = True


class OpportunityDismissalRequestSchema(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    reason: DismissalReason
    usefulness: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)
    evaluation_eligible: bool = True


class OpportunityFeedbackResponseSchema(ApiSchema):
    id: UUID
    opportunity_id: UUID
    reason_code: str
    usefulness: int | None
    comment: str | None
    evaluation_eligible: bool
    created_at: datetime
