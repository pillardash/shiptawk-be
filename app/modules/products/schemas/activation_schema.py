from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.shared.schemas import ApiSchema


class ActivationStageName(StrEnum):
    product = "product"
    profile = "profile"
    search = "search"
    first_run = "first_run"


class ActivationStageStatus(StrEnum):
    pending = "pending"
    blocked = "blocked"
    in_progress = "in_progress"
    complete = "complete"


class ActivationCommand(StrEnum):
    select_product = "select_product"
    attach_github = "attach_github"
    monitor_repositories = "monitor_repositories"
    choose_repository_evidence = "choose_repository_evidence"
    approve_profile = "approve_profile"
    connect_website = "connect_website"
    crawl_website = "crawl_website"
    connect_search = "connect_search"
    defer_search = "defer_search"
    finalize_and_start = "finalize_and_start"
    view_run = "view_run"
    view_report = "view_report"


class SearchMode(StrEnum):
    connected = "connected"
    deferred_reduced = "deferred_reduced"
    required = "required"


class RepositoryEvidenceDecision(StrEnum):
    connected = "connected"
    deferred = "deferred"


class EvidenceMode(StrEnum):
    website_search = "website_search"
    website_github = "website_github"
    website_search_github = "website_search_github"


class CapabilityStatus(StrEnum):
    ready = "ready"
    not_configured = "not_configured"
    syncing = "syncing"
    stale = "stale"
    failed = "failed"
    revoked = "revoked"
    unavailable = "unavailable"


class SourceCapability(ApiSchema):
    source: Literal["website", "search", "github"]
    required: bool
    status: CapabilityStatus
    limitation: str | None = None


class CapabilityReadiness(ApiSchema):
    evidence_mode: EvidenceMode | None
    sources: list[SourceCapability]
    required_blockers: list[str]
    source_recovery: list[str]
    optional_enhancements: list[str]
    available_recommendation_classes: list[str]
    unavailable_recommendation_classes: list[str]


class ActivationStageProjection(ApiSchema):
    stage: ActivationStageName
    status: ActivationStageStatus
    blockers: list[str]
    allowed_commands: list[ActivationCommand]
    canonical_href: str
    completed_at: datetime | None = None


class ActivationRunSummary(ApiSchema):
    id: UUID
    status: Literal["pending", "running", "completed", "failed", "stopped"]
    current_stage: str | None
    canonical_href: str


class ActivationReportSummary(ApiSchema):
    run_id: UUID
    plan_id: UUID
    revision: int
    period_start: datetime
    period_end: datetime
    finalized_at: datetime
    canonical_href: str


class ActivationProjection(ApiSchema):
    schema_version: Literal[1] = 1
    workspace_id: UUID
    activation_product_id: UUID | None
    onboarding_complete: bool
    search_mode: SearchMode | None
    repository_evidence_decision: RepositoryEvidenceDecision | None
    readiness: CapabilityReadiness | None
    reduced_capability_allowed: bool
    search_provider_available: bool
    stages: list[ActivationStageProjection]
    active_run: ActivationRunSummary | None
    current_run: ActivationRunSummary | None
    latest_finalized_report: ActivationReportSummary | None
    first_run_state: Literal["not_started", "pending", "running", "completed", "failed", "stopped"]
    next_schedule: datetime | None
    canonical_destination: str


class ActivationProductCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    product_id: UUID


class SearchModeCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    mode: SearchMode


class RepositoryEvidenceModeCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    mode: RepositoryEvidenceDecision


class ActivationFinalizeCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    scheduled_enabled: bool | None = None
    email_enabled: bool | None = None
    timezone: Annotated[str | None, Field(default=None, min_length=1, max_length=64)]
    weekday: Annotated[int | None, Field(default=None, ge=0, le=6)]
    hour: Annotated[int | None, Field(default=None, ge=0, le=23)]


class ActivationFinalizeResponse(ApiSchema):
    schema_version: Literal[1] = 1
    replayed: bool
    run: ActivationRunSummary
    canonical_destination: str
    activation: ActivationProjection
