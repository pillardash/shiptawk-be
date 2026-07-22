from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from app.shared.schemas import ApiSchema


class Confidence(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class ApprovalLevel(StrEnum):
    safe = "safe"
    review = "review"
    required = "required"


class RecommendationEnvelope(ApiSchema):
    evidence_ids: list[UUID]
    interpretation: Annotated[str, Field(min_length=1, max_length=4000)]
    recommendation: Annotated[str, Field(min_length=1, max_length=4000)]
    confidence: Confidence
    missing_information: list[Annotated[str, Field(min_length=1, max_length=500)]]
    approval_level: ApprovalLevel
    measurement_window: Annotated[str, Field(min_length=1, max_length=64)]
    stop_conditions: list[Annotated[str, Field(min_length=1, max_length=500)]]

    @model_validator(mode="after")
    def require_evidence(self) -> "RecommendationEnvelope":
        if not self.evidence_ids:
            raise ValueError("A recommendation requires at least one evidence item.")
        return self


class OpportunityCreate(RecommendationEnvelope):
    product_id: UUID | None = None
    title: Annotated[str, Field(min_length=1, max_length=300)]
    impact_score: Annotated[int, Field(ge=0, le=100)]
    effort_score: Annotated[int, Field(ge=0, le=100)]
    urgency_score: Annotated[int, Field(ge=0, le=100)]
    cooldown_dedup_key: Annotated[str, Field(min_length=1, max_length=255)]
    cooldown_days: Annotated[int, Field(ge=1, le=365)] = 28
    provenance: dict[str, object]


class OpportunityResponse(RecommendationEnvelope):
    id: UUID
    workspace_id: UUID
    product_id: UUID | None
    operator_run_id: UUID | None
    title: str
    impact_score: int
    effort_score: int
    urgency_score: int
    status: str
    cooldown_dedup_key: str
    cooldown_until: datetime
    provenance: dict[str, object]
    created_at: datetime
    updated_at: datetime


class OpportunityListResponse(ApiSchema):
    items: list[OpportunityResponse]


class OperatorRunCreate(ApiSchema):
    product_id: UUID | None = None
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)]
    trigger: Annotated[str, Field(pattern="^(manual|scheduled|significant_event)$")] = "manual"


class OperatorRunResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID | None
    trigger: str
    status: str
    idempotency_key: str
    analyzer_version: str
    provenance: dict[str, object]
    summary: dict[str, object]
    started_at: datetime
    completed_at: datetime | None


class OperatorRunListResponse(ApiSchema):
    items: list[OperatorRunResponse]


class PlanCreate(ApiSchema):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    opportunity_ids: Annotated[list[UUID], Field(max_length=3)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)]


class PlanActionResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    plan_id: UUID
    opportunity_id: UUID | None
    title: str
    description: str
    evidence_ids: list[UUID]
    confidence: Confidence
    missing_information: list[str]
    approval_level: ApprovalLevel
    approval_status: str
    status: str
    cooldown_dedup_key: str
    idempotency_key: str
    provenance: dict[str, object]
    created_at: datetime
    updated_at: datetime


class PlanResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    operator_run_id: UUID | None
    title: str
    status: str
    period_start: datetime
    period_end: datetime
    idempotency_key: str
    provenance: dict[str, object]
    created_at: datetime
    updated_at: datetime
    actions: list[PlanActionResponse] = Field(default_factory=list)


class PlanListResponse(ApiSchema):
    items: list[PlanResponse]


class ApprovalDecision(ApiSchema):
    reason: Annotated[str, Field(min_length=1, max_length=1000)] | None = None


class ApprovalRequestResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    action_id: UUID
    status: str
    reason: str | None
    requested_by_actor_id: UUID
    decided_by_actor_id: UUID | None
    requested_at: datetime
    decided_at: datetime | None
    provenance: dict[str, object]


class ExecutionCreate(ApiSchema):
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)]


class ExecutionResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    action_id: UUID
    approval_request_id: UUID | None
    status: str
    idempotency_key: str
    output: dict[str, object]
    provenance: dict[str, object]
    started_at: datetime
    completed_at: datetime | None


class VerificationResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    execution_run_id: UUID
    status: str
    method: str
    result: dict[str, object]
    idempotency_key: str
    provenance: dict[str, object]
    verified_at: datetime


class MeasurementResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    action_id: UUID
    status: str
    starts_at: datetime
    ends_at: datetime
    metric_contract: dict[str, object]
    baseline: dict[str, object]
    result: dict[str, object] | None
    idempotency_key: str
    provenance: dict[str, object]
    created_at: datetime
