from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.modules.operator.schemas.ai_output_schema import (
    BlogBriefContent,
    ClaimEvidence,
    PreparedAssetOutput,
    ProductUpdateContent,
    SeoPageUpdateContent,
    XDraftContent,
)
from app.shared.schemas import ApiSchema


class ActionStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    dismissed = "dismissed"


class AssetKind(StrEnum):
    seo_page_update = "seo_page_update"
    blog_brief = "blog_brief"
    product_update = "product_update"
    x_draft = "x_draft"


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    stopped = "stopped"


class HistoryStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    running = "running"
    executing = "executing"
    scheduled = "scheduled"
    measuring = "measuring"
    completed = "completed"
    failed = "failed"
    stopped = "stopped"
    cancelled = "cancelled"
    dismissed = "dismissed"
    unavailable = "unavailable"


class SourceHealthStatus(StrEnum):
    not_connected = "not_connected"
    healthy = "healthy"
    reduced = "reduced"
    stale = "stale"
    syncing = "syncing"
    error = "error"
    revoked = "revoked"


class SourceHealthProjection(ApiSchema):
    github: SourceHealthStatus
    website: SourceHealthStatus
    search_console: SourceHealthStatus
    observed_at: datetime | None = None


class RecoverySource(StrEnum):
    github_installation = "github_installation"
    github_monitoring = "github_monitoring"
    website = "website"
    search_console = "search_console"


class RecoveryCommand(StrEnum):
    attach_github = "attach_github"
    enable_monitoring = "enable_monitoring"
    retry_website = "retry_website"
    reconnect_search = "reconnect_search"
    select_search_property = "select_search_property"
    retry_search_sync = "retry_search_sync"
    inspect_source = "inspect_source"


class PreservedState(StrEnum):
    product_profile = "product_profile"
    prior_reports = "prior_reports"
    prepared_assets = "prepared_assets"
    approvals = "approvals"
    source_configuration = "source_configuration"


class SourceRecoveryProjection(ApiSchema):
    source: RecoverySource
    status: SourceHealthStatus
    code: str
    observed_at: datetime | None = None
    last_successful_at: datetime | None = None
    retryable: bool
    preserved_state: list[PreservedState]
    affected_capability: str
    recovery_command: RecoveryCommand
    href: str


class ApprovalCounts(ApiSchema):
    awaiting_approval: int = 0
    approved: int = 0
    rejected: int = 0


class SummaryProjection(ApiSchema):
    status: Literal["available", "unavailable", "no_changes"] = "no_changes"
    reason: str | None = None
    headline: str | None = None
    items: list[str] = Field(default_factory=list)


class CorrectiveActionProjection(ApiSchema):
    reason: str
    href: str | None = None


class NoRecommendationOutcomeProjection(ApiSchema):
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


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
    status: RunStatus
    current_stage: str | None
    stage_summary: dict[str, object]
    failure_category: str | None
    period_start: datetime | None
    period_end: datetime | None
    started_at: datetime
    completed_at: datetime | None
    stopped_reason: str | None = None
    corrective_action: CorrectiveActionProjection | None = None


class RunDetailProjection(RunProjection):
    product_id: UUID
    plan_id: UUID | None
    plan_revision: int | None
    profile_revision: int | None
    source_freshness: dict[str, object]
    shipped_summary: dict[str, object]
    search_summary: dict[str, object]
    actions: list["ActionProjection"]
    no_recommendation_outcomes: list[dict[str, object]]


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
    dismissal_reason: str | None
    dismissal_comment: str | None
    dismissed_by_actor_id: UUID | None
    dismissed_at: datetime | None
    created_at: datetime
    completed_at: datetime | None
    product_id: UUID | None = None
    recommendation_decision_authority: Literal["recommendation_command"] = "recommendation_command"
    asset_approval_authority: Literal["exact_asset_revision"] = "exact_asset_revision"
    execution_authority: Literal["action_command"] = "action_command"
    valid_commands: list[Literal["approve_asset_revision", "dismiss", "start", "complete"]] = Field(
        default_factory=list
    )


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
    state: Literal["proposed", "accepted", "dismissed", "superseded"]
    valid_commands: list[Literal["accept", "dismiss"]]
    decision_history: list["RecommendationDecisionProjection"] = Field(default_factory=list)
    current_actor_feedback: "RecommendationUsefulnessFeedbackProjection | None" = None
    created_at: datetime


class RecommendationDecisionProjection(ApiSchema):
    id: UUID
    decision: Literal["accepted", "dismissed"]
    reason: str | None
    comment: str | None
    actor_id: UUID
    created_at: datetime


class RecommendationRating(StrEnum):
    useful = "useful"
    not_useful = "not_useful"
    unsure = "unsure"


class RecommendationFeedbackReason(StrEnum):
    actionable = "actionable"
    new_insight = "new_insight"
    clear_evidence = "clear_evidence"
    not_relevant = "not_relevant"
    already_known = "already_known"
    incorrect = "incorrect"
    insufficient_evidence = "insufficient_evidence"
    too_generic = "too_generic"
    not_actionable = "not_actionable"
    duplicate = "duplicate"
    other = "other"


class RecommendationUsefulnessFeedbackCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    schema_version: Literal[1] = 1
    rating: RecommendationRating
    reason: RecommendationFeedbackReason | None = None
    comment: Annotated[str | None, Field(default=None, max_length=500)]

    @model_validator(mode="after")
    def reason_matches_rating(self) -> "RecommendationUsefulnessFeedbackCommand":
        useful = {
            RecommendationFeedbackReason.actionable,
            RecommendationFeedbackReason.new_insight,
            RecommendationFeedbackReason.clear_evidence,
            RecommendationFeedbackReason.other,
        }
        negative = {
            RecommendationFeedbackReason.not_relevant,
            RecommendationFeedbackReason.already_known,
            RecommendationFeedbackReason.incorrect,
            RecommendationFeedbackReason.insufficient_evidence,
            RecommendationFeedbackReason.too_generic,
            RecommendationFeedbackReason.not_actionable,
            RecommendationFeedbackReason.duplicate,
            RecommendationFeedbackReason.other,
        }
        if (
            self.reason is not None
            and self.rating == RecommendationRating.useful
            and self.reason not in useful
        ):
            raise ValueError("reason is not valid for a useful rating")
        if (
            self.reason is not None
            and self.rating == RecommendationRating.not_useful
            and self.reason not in negative
        ):
            raise ValueError("reason is not valid for a not_useful rating")
        if self.rating == RecommendationRating.unsure and self.reason not in (
            None,
            RecommendationFeedbackReason.other,
        ):
            raise ValueError("unsure only supports the other reason")
        return self


class RecommendationUsefulnessFeedbackProjection(ApiSchema):
    schema_version: Literal[1] = 1
    id: UUID
    recommendation_id: UUID
    actor_id: UUID
    rating: RecommendationRating
    reason: RecommendationFeedbackReason | None
    comment: str | None
    created_at: datetime
    updated_at: datetime


class PlanViewProjection(ApiSchema):
    schema_version: Literal[1] = 1
    plan_id: UUID
    plan_revision: int
    period_start: datetime
    period_end: datetime
    first_viewed_at: datetime
    report_ordinal: int
    second_report_return: bool
    fourth_report_return: bool
    valid_no_recommendation_report: bool


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


AssetContent = Annotated[
    SeoPageUpdateContent | BlogBriefContent | ProductUpdateContent | XDraftContent,
    Field(union_mode="left_to_right"),
]


class SeoAssetPatch(ApiSchema):
    kind: Literal["seo_page_update"]
    content: SeoPageUpdateContent


class BlogAssetPatch(ApiSchema):
    kind: Literal["blog_brief"]
    content: BlogBriefContent


class ProductUpdateAssetPatch(ApiSchema):
    kind: Literal["product_update"]
    content: ProductUpdateContent


class XAssetPatch(ApiSchema):
    kind: Literal["x_draft"]
    content: XDraftContent


class LegacyXAssetPatch(ApiSchema):
    """Compatibility for the shipped X editor; new clients send the discriminator."""

    content: XDraftContent
    claim_evidence: list[ClaimEvidence] = Field(default_factory=list)


AssetPatchContent = (
    Annotated[
        SeoAssetPatch | BlogAssetPatch | ProductUpdateAssetPatch | XAssetPatch,
        Field(discriminator="kind"),
    ]
    | LegacyXAssetPatch
)


class AssetPatchCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    expected_revision: Annotated[int | None, Field(default=None, ge=1)]
    revision: Annotated[int | None, Field(default=None, ge=1)]
    structured_content: AssetPatchContent

    @model_validator(mode="after")
    def revision_is_present(self) -> "AssetPatchCommand":
        if self.expected_revision is None and self.revision is None:
            raise ValueError("expectedRevision is required.")
        if self.expected_revision is not None and self.revision not in (
            None,
            self.expected_revision,
        ):
            raise ValueError("revision and expectedRevision disagree.")
        return self

    @property
    def effective_revision(self) -> int:
        return self.expected_revision or self.revision or 0


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
    kind: AssetKind
    product_id: UUID
    structured_content: PreparedAssetOutput
    risk_outcome: str | None
    approval_status: str | None
    approval_required: bool
    is_current: bool
    current_asset_id: UUID
    current_revision: int
    superseding_asset_id: UUID | None
    evidence_references: list[str]
    created_at: datetime
    rejected_reason: str | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    valid_commands: list[Literal["edit", "approve", "reject", "publish"]] = Field(
        default_factory=list
    )
    x_bridge: "XAssetBridgeProjection | None" = None


class XAssetBridgeProjection(ApiSchema):
    state: Literal["not_applicable", "unavailable", "linked"]
    legacy_draft_id: UUID | None = None
    asset_revision: int | None = None
    publishing_authority: Literal["legacy_x_draft"] = "legacy_x_draft"
    reason: str | None = None


class PageInfo(ApiSchema):
    limit: int
    offset: int
    total: int
    has_next: bool


class AssetListResponse(ApiSchema):
    items: list[AssetProjection]
    page: PageInfo


class ActionListResponse(ApiSchema):
    items: list[ActionProjection]
    page: PageInfo


class SafeEvidenceProjection(ApiSchema):
    id: UUID
    source_type: str
    source_record_type: str
    period: dict[str, object]
    observed_at: datetime
    freshness_status: str
    public_safe_summary: str
    metrics: dict[str, object]
    comparison: dict[str, object] | None = None
    provenance: dict[str, object]
    redaction_status: Literal["public_safe", "redacted"] = "public_safe"


class ActionDetailResponse(ApiSchema):
    action: ActionProjection
    recommendation: RecommendationProjection | None
    evidence: list[SafeEvidenceProjection]
    interpretation: str | None
    recommendation_text: str | None
    prepared_output: AssetProjection | None
    expected_metric: dict[str, object]
    measurement_window_days: int | None
    confidence: str
    estimated_effort: str | None
    missing_information: list[str]
    approval_requirement: str
    approval_state: str
    action_history: list[dict[str, object]]
    measurement: "MeasurementProjection | None"


class OperatorReadinessProjection(ApiSchema):
    product_id: UUID
    operator_enabled: bool
    manual_enabled: bool
    scheduled_enabled: bool
    email_enabled: bool
    timezone: str
    weekday: int
    hour: int
    approved_profile_id: UUID | None
    approved_profile_revision: int | None
    website_readiness: dict[str, object]
    search_readiness: dict[str, object]
    search_reduced_mode: bool
    search_mode: Literal["connected", "deferred_reduced", "required"]
    repository_evidence_ready: bool
    evidence_mode: Literal["website_search", "website_github", "website_search_github"] | None
    optional_enhancements: list[str] = Field(default_factory=list)
    manual_run_available: bool
    blockers: list[str]
    active_run_id: UUID | None
    next_schedule: datetime | None
    source_recovery: list[SourceRecoveryProjection] = Field(default_factory=list)


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
    product_id: UUID | None = None
    product_name: str | None = None


class MeasurementListResponse(ApiSchema):
    items: list[MeasurementProjection]
    page: PageInfo


class PlanListResponse(ApiSchema):
    items: list[PlanProjection]
    page: PageInfo


class HistoryEntryProjection(ApiSchema):
    id: UUID
    product_id: UUID
    product_name: str
    entry_type: Literal["run", "action", "measurement"]
    status: HistoryStatus
    title: str
    occurred_at: datetime
    resource_id: UUID
    action_id: UUID | None = None
    canonical_destination: str


class HistoryListResponse(ApiSchema):
    items: list[HistoryEntryProjection]
    page: PageInfo


class TodayProjection(ApiSchema):
    schema_version: Literal[1] = 1
    plan: PlanProjection | None
    run: RunProjection | None
    latest_finalized_report: PlanProjection | None
    current_run: RunProjection | None
    product_id: UUID
    readiness: OperatorReadinessProjection
    manual_run_state: Literal["available", "blocked", "active"]
    manual_run_reason: str | None
    source_health: SourceHealthProjection
    next_schedule: datetime | None
    approval_counts: ApprovalCounts
    shipped_summary: SummaryProjection
    search_summary: SummaryProjection
    no_recommendation_outcomes: list[NoRecommendationOutcomeProjection]
    recent_completed: list[ActionProjection]
    recent_measurements: list[MeasurementProjection]
    source_recovery: list[SourceRecoveryProjection] = Field(default_factory=list)


class OperatorConfigPatch(ApiSchema):
    enabled: bool | None = None
    manual_enabled: bool | None = None
    scheduled_enabled: bool | None = None
    email_enabled: bool | None = None
    timezone: Annotated[str | None, Field(default=None, min_length=1, max_length=64)]
    weekday: Annotated[int | None, Field(default=None, ge=0, le=6)]
    hour: Annotated[int | None, Field(default=None, ge=0, le=23)]
