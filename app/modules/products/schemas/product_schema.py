from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from app.modules.products.policies.product_policy import validate_public_website_url
from app.shared.schemas import ApiSchema

ProductText = Annotated[str, StringConstraints(strip_whitespace=True)]
RepoRole = Literal[
    "frontend", "backend", "mobile", "worker", "infra", "docs", "shared_lib", "unknown"
]


class ProductWrite(ApiSchema):
    name: ProductText = Field(min_length=1, max_length=120)
    description: ProductText = Field(default="", max_length=1200)
    website_url: ProductText = Field(default="", max_length=500)
    target_audience: ProductText = Field(
        default="", max_length=180, description="Legacy marketing field; use productProfile."
    )
    messaging_angle: ProductText = Field(
        default="", max_length=220, description="Legacy marketing field; use productProfile."
    )
    tone_override: Literal["casual", "technical", "hype"] | None = Field(
        default=None, description="Legacy marketing field; use productProfile."
    )
    blocked_terms: list[Annotated[ProductText, StringConstraints(min_length=1, max_length=120)]] = (
        Field(default_factory=list, max_length=80)
    )
    blocked_topics: list[
        Annotated[ProductText, StringConstraints(min_length=1, max_length=120)]
    ] = Field(default_factory=list, max_length=80)
    safe_public_boundaries: list[
        Annotated[ProductText, StringConstraints(min_length=1, max_length=120)]
    ] = Field(default_factory=list, max_length=80)
    primary_customer_pain: ProductText = Field(default="", max_length=240)
    desired_outcome: ProductText = Field(default="", max_length=240)
    positioning_statement: ProductText = Field(default="", max_length=300)
    proof_points: list[Annotated[ProductText, StringConstraints(min_length=1, max_length=180)]] = (
        Field(default_factory=list, max_length=20)
    )
    customer_use_cases: list[
        Annotated[ProductText, StringConstraints(min_length=1, max_length=180)]
    ] = Field(default_factory=list, max_length=20)
    content_goal: Literal[
        "", "awareness", "waitlist", "trial", "retention", "launch", "feedback"
    ] = ""
    cta_preference: ProductText = Field(default="", max_length=180)
    founder_story_angle: ProductText = Field(default="", max_length=240)

    @field_validator("website_url")
    @classmethod
    def validate_public_website_url(cls, value: str) -> str:
        return validate_public_website_url(value)


class ProductRepositoryAssignment(ApiSchema):
    repo_role: RepoRole = "unknown"
    is_primary_repo: bool = False


class ProductRepositoryRoleDescriptionUpdate(ApiSchema):
    role_description_override: ProductText | None = Field(default=None, max_length=600)

    @field_validator("role_description_override")
    @classmethod
    def empty_override_is_none(cls, value: str | None) -> str | None:
        return value or None


class ProductResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    target_audience: str | None
    messaging_angle: str | None
    tone_override: str | None
    blocked_terms: list[str]
    blocked_topics: list[str]
    safe_public_boundaries: list[str]
    website_url: str | None
    primary_customer_pain: str | None
    desired_outcome: str | None
    positioning_statement: str | None
    proof_points: list[str]
    customer_use_cases: list[str]
    content_goal: str
    cta_preference: str | None
    founder_story_angle: str | None
    created_at: datetime
    updated_at: datetime


class ProductRepositoryResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    repo_id: UUID
    generated_role_description: str | None
    role_description_override: str | None
    role_description_generated_at: datetime | None
    role_description_generation_version: int | None
    repo_role: str
    is_primary_repo: bool
    created_at: datetime


class ProductWorkspaceResponse(ApiSchema):
    products: list[ProductResponse]
    product_repositories: list[ProductRepositoryResponse]


class ProductOperatorSummary(ApiSchema):
    product_id: UUID
    product_name: str
    ready: bool
    blockers: list[str]
    repository_ready: bool
    website_status: Literal["not_connected", "healthy", "stale", "disconnected"]
    search_status: Literal["not_connected", "healthy", "reduced", "stale", "disconnected"]
    latest_run_id: UUID | None
    latest_run_status: str | None
    latest_plan_id: UUID | None
    latest_plan_revision: int | None
    open_action_count: int
    awaiting_approval_count: int
    next_schedule: datetime | None
    next_setup_action: str | None


class ProductOperatorSummaryList(ApiSchema):
    items: list[ProductOperatorSummary]


class ProductContextGenerationRequest(ApiSchema):
    website_url: ProductText = Field(default="", max_length=500)
    force: bool = False
    idempotency_key: ProductText = Field(min_length=1, max_length=255)

    @field_validator("website_url")
    @classmethod
    def validate_public_website_url(cls, value: str) -> str:
        return validate_public_website_url(value)


class ProductContextGeneratedOutput(ApiSchema):
    name: ProductText = Field(min_length=1, max_length=120)
    description: ProductText = Field(min_length=1, max_length=1200)
    target_audience: ProductText = Field(min_length=1, max_length=180)
    messaging_angle: ProductText = Field(min_length=1, max_length=220)
    tone_override: Literal["", "casual", "technical", "hype"] = ""
    safe_public_boundaries: list[
        Annotated[ProductText, StringConstraints(min_length=1, max_length=120)]
    ] = Field(default_factory=list, max_length=80)
    primary_customer_pain: ProductText = Field(default="", max_length=240)
    desired_outcome: ProductText = Field(default="", max_length=240)
    positioning_statement: ProductText = Field(default="", max_length=300)
    proof_points: list[Annotated[ProductText, StringConstraints(min_length=1, max_length=180)]] = (
        Field(default_factory=list, max_length=20)
    )
    customer_use_cases: list[
        Annotated[ProductText, StringConstraints(min_length=1, max_length=180)]
    ] = Field(default_factory=list, max_length=20)
    content_goal: Literal[
        "", "awareness", "waitlist", "trial", "retention", "launch", "feedback"
    ] = ""
    cta_preference: ProductText = Field(default="", max_length=180)
    founder_story_angle: ProductText = Field(default="", max_length=240)
    confidence: int = Field(ge=0, le=100)


class ProductContextPrefillDraft(ApiSchema):
    name: str
    description: str
    website_url: str
    target_audience: str
    messaging_angle: str
    tone_override: Literal["", "casual", "technical", "hype"]
    blocked_terms: str
    blocked_topics: str
    safe_public_boundaries: str
    primary_customer_pain: str
    desired_outcome: str
    positioning_statement: str
    proof_points: str
    customer_use_cases: str
    content_goal: str
    cta_preference: str
    founder_story_angle: str


class ProductContextGenerationResponse(ApiSchema):
    draft: ProductContextPrefillDraft
    source_note: str
    confidence: int
    needs_review: bool
