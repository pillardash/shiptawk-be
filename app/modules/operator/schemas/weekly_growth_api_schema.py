from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.shared.schemas import ApiSchema


class ActionStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    dismissed = "dismissed"


class DecisionReason(StrEnum):
    not_relevant = "not_relevant"
    already_done = "already_done"
    not_now = "not_now"
    insufficient_evidence = "insufficient_evidence"
    incorrect_interpretation = "incorrect_interpretation"
    too_much_effort = "too_much_effort"
    duplicate = "duplicate"
    wrong_target_page = "wrong_target_page"
    other = "other"


class WeeklyRunRequest(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    run_kind: Literal["weekly_growth"]


class RunProjection(ApiSchema):
    schema_version: Literal[1] = 1
    id: UUID
    run_kind: str
    trigger: str
    status: str
    current_stage: str | None
    stage_summary: dict[str, object]
    failure_category: str | None
    period_start: datetime | None
    period_end: datetime | None
    started_at: datetime
    completed_at: datetime | None


class ActionProjection(ApiSchema):
    schema_version: Literal[1] = 1
    id: UUID
    plan_id: UUID
    recommendation_id: UUID | None
    position: int | None
    title: str
    description: str
    confidence: str
    approval_level: str
    approval_status: str
    status: ActionStatus
    prepared_output_type: str | None
    expected_metric: dict[str, object]
    measurement_due_at: datetime | None
    approved_asset_id: UUID | None
    approved_asset_revision: int | None
    created_at: datetime
    completed_at: datetime | None


class PlanProjection(ApiSchema):
    schema_version: Literal[1] = 1
    id: UUID
    operator_run_id: UUID
    title: str
    status: Literal["ready"]
    revision: int
    period_start: datetime
    period_end: datetime
    finalized_at: datetime
    action_count: int
    no_recommendation_count: int
    shipped_summary: dict[str, object]
    search_summary: dict[str, object]
    actions: list[ActionProjection] = Field(default_factory=list, max_length=3)


class RecommendationProjection(ApiSchema):
    schema_version: Literal[1] = 1
    id: UUID
    opportunity_id: UUID
    operator_run_id: UUID
    kind: str
    title: str | None
    rationale: str | None
    recommendation: str | None
    confidence: str | None
    evidence_ids: list[str]
    accepted_at: datetime | None
    created_at: datetime


class ReasonCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    reason: DecisionReason
    comment: Annotated[str | None, Field(default=None, max_length=500)]


class ActionApprovalCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    asset_id: UUID
    revision: Annotated[int, Field(ge=1)]


class AssetPatchCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    revision: Annotated[int, Field(ge=1)]
    structured_content: dict[str, object]


class AssetDecisionCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    revision: Annotated[int, Field(ge=1)]
    reason: Annotated[str | None, Field(default=None, max_length=500)]


class AssetProjection(ApiSchema):
    schema_version: Literal[1] = 1
    id: UUID
    recommendation_id: UUID
    action_id: UUID | None
    revision: int
    kind: str
    structured_content: dict[str, object]
    risk_outcome: str | None
    approval_status: str | None
    created_at: datetime


class MeasurementProjection(ApiSchema):
    schema_version: Literal[1] = 1
    id: UUID
    action_id: UUID
    status: str
    starts_at: datetime
    ends_at: datetime
    due_at: datetime | None
    expected_metric: dict[str, object]
    baseline: dict[str, object]
    result: dict[str, object] | None
    outcome: str | None
    limitations: list[str]


class TodayProjection(ApiSchema):
    schema_version: Literal[1] = 1
    plan: PlanProjection | None
    run: RunProjection | None
    source_health: dict[str, object]
    next_schedule: datetime | None
    approval_counts: dict[str, int]
    shipped_summary: dict[str, object]
    search_summary: dict[str, object]
    recent_completed: list[ActionProjection]
