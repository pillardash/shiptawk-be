from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.operator.enums import (
    ActionFamily,
    EffortBand,
    EvaluationOutcome,
    EvaluationReason,
    EvidenceSourceKind,
    FreshnessStatus,
    OpportunityStatus,
    OpportunityType,
)

Score = Annotated[Decimal, Field(ge=0, le=100)]
MetricValue = Decimal | int | str | bool | None


class ImmutableContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProfileCapabilitySnapshot(ImmutableContract):
    capability_key: str
    name: str
    description: str | None = None
    active: bool = True


class ProductProfileSnapshot(ImmutableContract):
    product_name: str | None = None
    profile_id: UUID | None = None
    profile_version: int = 1
    schema_version: str = "1"
    status: Literal["draft", "approved", "archived"] = "approved"
    completeness: Score = Decimal("100")
    objective: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    audience: str | None = None
    conversion_action: str | None = None
    conversion_url: str | None = None
    positioning: str | None = None
    capabilities: tuple[ProfileCapabilitySnapshot, ...] = ()


class MetricSnapshot(ImmutableContract):
    clicks: int = Field(ge=0)
    impressions: int = Field(ge=0)
    ctr: Decimal = Field(ge=0, le=1)
    average_position: Decimal = Field(ge=0)


class QueryPerformanceSnapshot(ImmutableContract):
    query_fingerprint: str
    query_text: str
    current: MetricSnapshot
    prior: MetricSnapshot
    page_fingerprints: tuple[str, ...] = ()
    matched_page_fingerprint: str | None = None
    page_match_status: Literal["matched", "unmatched", "ambiguous"] = "unmatched"
    profile_relevance: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    branded: bool = False
    navigational: bool = False


class SearchPagePerformanceSnapshot(ImmutableContract):
    url_fingerprint: str
    url: str
    website_page_fingerprint: str | None = None
    match_status: Literal["matched", "unmatched", "ambiguous"] = "unmatched"
    current: MetricSnapshot
    prior: MetricSnapshot


class SearchSnapshot(ImmutableContract):
    source_id: UUID | None = None
    sync_id: UUID | None = None
    health_status: Literal["healthy", "degraded", "failed", "revoked"] = "healthy"
    baseline_status: Literal["ready", "building", "failed"] = "ready"
    current_start: date | None = None
    current_end: date | None = None
    prior_start: date | None = None
    prior_end: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    comparison_start: date | None = None
    comparison_end: date | None = None
    missing_dates: tuple[date, ...] = ()
    complete: bool = True
    truncated: bool = False
    warnings: tuple[str, ...] = ()
    current: MetricSnapshot | None = None
    prior: MetricSnapshot | None = None
    queries: tuple[QueryPerformanceSnapshot, ...] = ()
    pages: tuple[SearchPagePerformanceSnapshot, ...] = ()


class QuerySnapshot(ImmutableContract):
    query_key: str
    query: str
    metrics: dict[str, Decimal] = Field(default_factory=dict)


class PageSnapshot(ImmutableContract):
    page_key: str
    url: str
    metrics: dict[str, Decimal] = Field(default_factory=dict)


class WebsitePageSnapshot(ImmutableContract):
    page_fingerprint: str
    url: str
    title: str | None = None
    meta_description: str | None = None
    headings: tuple[str, ...] = ()
    h1s: tuple[str, ...] = ()
    indexable: bool | None = None
    crawl_status: Literal["succeeded", "failed", "blocked"] = "succeeded"
    canonical_url: str | None = None
    canonical_fingerprint: str | None = None
    page_type: Literal["homepage", "product", "pricing", "conversion", "other"] = "other"
    internal_links: tuple[str, ...] | None = None


class CapabilityMappingSnapshot(ImmutableContract):
    capability_key: str
    page_fingerprint: str
    profile_version: int
    coverage_score: Decimal = Field(ge=0, le=1)
    relevance_score: Decimal = Field(ge=0, le=1)
    status: Literal["missing", "partial", "complete"]


class WebsiteSnapshot(ImmutableContract):
    source_id: UUID | None = None
    crawl_id: UUID | None = None
    profile_version: int = 1
    status: Literal["pending", "running", "succeeded", "failed"] = "succeeded"
    health_status: Literal["healthy", "degraded", "failed"] = "healthy"
    completed_at: datetime | None = None
    complete: bool = True
    warnings: tuple[str, ...] = ()
    pages: tuple[WebsitePageSnapshot | PageSnapshot, ...] = ()
    capability_mappings: tuple[CapabilityMappingSnapshot, ...] = ()


class ShippingEventSnapshot(ImmutableContract):
    event_id: UUID | None = None
    event_key: str
    repository_id: UUID | None = None
    mapping_id: UUID | None = None
    event_type: str = "release"
    occurred_at: datetime
    safe_summary: str | None = None
    summary: str | None = None
    user_benefit: str | None = None
    evaluation_outcome: Literal["generate", "ignore", "review"] = "generate"
    public_worthiness_score: Score = Decimal("100")
    capability_key: str | None = None
    capability_coverage_score: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    capability_relevance_score: Decimal = Field(default=Decimal("1"), ge=0, le=1)


class ShippingSnapshot(ImmutableContract):
    window_start: datetime | None = None
    window_end: datetime | None = None
    events: tuple[ShippingEventSnapshot, ...] = ()


class OpportunityHistoryItem(ImmutableContract):
    identity_fingerprint: str
    action_family: ActionFamily | None = None
    opportunity_type: OpportunityType | None = None
    status: OpportunityStatus
    occurred_at: datetime
    dismissal_reason: str | None = None
    cooldown_until: datetime | None = None
    confidence_score: Score | None = None
    strategic_fit_score: Score | None = None
    priority_score: Score | None = None


class OpportunityHistorySnapshot(ImmutableContract):
    opportunities: tuple[OpportunityHistoryItem, ...] = ()


class DetectionInput(ImmutableContract):
    schema_version: str
    workspace_id: UUID
    product_id: UUID
    as_of: datetime
    profile: ProductProfileSnapshot
    search: SearchSnapshot | None = None
    queries: tuple[QuerySnapshot, ...] = ()
    pages: tuple[PageSnapshot, ...] = ()
    website: WebsiteSnapshot | None = None
    shipping: ShippingSnapshot = Field(default_factory=ShippingSnapshot)
    history: OpportunityHistorySnapshot = Field(default_factory=OpportunityHistorySnapshot)


class EvidenceSnapshot(ImmutableContract):
    evidence_key: Annotated[str, Field(min_length=1, max_length=255)]
    source_type: EvidenceSourceKind
    source_record_type: Annotated[str, Field(min_length=1, max_length=64)]
    source_id: UUID | None = None
    source_run_id: UUID | None = None
    observed_at: datetime
    period: dict[str, MetricValue] = Field(default_factory=dict)
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    public_safe_summary: Annotated[str, Field(min_length=1, max_length=1000)]
    confidence_score: Score
    freshness_status: FreshnessStatus
    quality_warnings: tuple[str, ...] = ()


class ScoreFeatures(ImmutableContract):
    impact: Score
    confidence: Score
    effort: Score
    urgency: Score
    strategic_fit: Score
    novelty: Score
    priority: Score


class DetectorCandidate(ImmutableContract):
    opportunity_type: OpportunityType
    action_family: ActionFamily
    subject_kind: str
    subject_keys: dict[str, str]
    identity_fingerprint: str
    observation_fingerprint: str
    title: Annotated[str, Field(min_length=1, max_length=300)]
    interpretation: Annotated[str, Field(min_length=1, max_length=4000)]
    recommended_action: Annotated[str, Field(min_length=1, max_length=4000)]
    expected_metric: dict[str, MetricValue]
    effort_band: EffortBand
    evidence: tuple[EvidenceSnapshot, ...]
    scores: ScoreFeatures


class DetectorEvaluation(ImmutableContract):
    detector_id: str
    detector_version: str
    outcome: EvaluationOutcome
    reason_codes: tuple[EvaluationReason, ...] = ()
    candidate: DetectorCandidate | None = None
    diagnostics: dict[str, MetricValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def accepted_requires_candidate(self) -> "DetectorEvaluation":
        if self.outcome == EvaluationOutcome.accepted and self.candidate is None:
            raise ValueError("An accepted evaluation requires a candidate.")
        return self


class DetectionResult(ImmutableContract):
    input_hash: str
    evaluations: tuple[DetectorEvaluation, ...]
