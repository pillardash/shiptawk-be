from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from app.modules.products.enums.website_intelligence_enum import (
    WebsiteCapabilityMappingStatus,
    WebsiteCrawlRunStatus,
    WebsiteCrawlTrigger,
    WebsitePageStatus,
    WebsiteSourceStatus,
)
from app.modules.products.policies.website_crawl_policy import MAX_CRAWL_PAGES
from app.modules.products.policies.website_url_policy import normalize_website_url
from app.shared.schemas import ApiSchema

PublicUrl = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class WebsiteSourceWrite(ApiSchema):
    base_url: PublicUrl
    sitemap_urls: list[PublicUrl] = Field(default_factory=list, max_length=20)
    blog_url: PublicUrl | None = None
    documentation_url: PublicUrl | None = None
    expected_updated_at: datetime | None = None

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return normalize_website_url(value)

    @field_validator("sitemap_urls")
    @classmethod
    def normalize_sitemaps(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize_website_url(value) for value in values))

    @field_validator("blog_url", "documentation_url")
    @classmethod
    def normalize_optional_url(cls, value: str | None) -> str | None:
        return normalize_website_url(value) if value else None


class WebsiteSourceResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    base_url: str
    normalized_base_url: str
    status: WebsiteSourceStatus
    sitemap_urls: list[str]
    blog_url: str | None
    documentation_url: str | None
    last_successful_crawl_at: datetime | None
    disconnected_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WebsiteCrawlRequest(ApiSchema):
    trigger: WebsiteCrawlTrigger = WebsiteCrawlTrigger.manual
    page_limit: int = Field(default=MAX_CRAWL_PAGES, ge=1, le=MAX_CRAWL_PAGES)


class WebsiteCrawlRunResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    source_id: UUID
    status: WebsiteCrawlRunStatus
    trigger: WebsiteCrawlTrigger
    schema_version: int
    page_limit: int
    pages_discovered: int
    pages_succeeded: int
    pages_failed: int
    summary: dict[str, object]
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class WebsitePageResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    source_id: UUID
    first_discovered_run_id: UUID
    url: str
    canonical_url: str
    status: WebsitePageStatus
    created_at: datetime
    updated_at: datetime


class WebsitePageRelevanceResponse(ApiSchema):
    page: WebsitePageResponse
    score: float
    matched_terms: list[str]
    method: Literal["lexical"] = "lexical"


class WebsiteHealthFailure(ApiSchema):
    url: str
    reason: str


class WebsiteHealthResponse(ApiSchema):
    crawl_run_id: UUID | None
    status: Literal["healthy", "degraded", "failed", "unknown"]
    total_count: int
    healthy_count: int
    blocked_urls: list[str]
    failures: list[WebsiteHealthFailure]


class WebsiteCapabilityMappingResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    source_id: UUID
    crawl_run_id: UUID
    page_id: UUID | None
    capability_key: str
    capability_name: str
    status: WebsiteCapabilityMappingStatus
    relevance_score: int
    confidence_score: int
    evidence: dict[str, object]
    mapping_version: str
    created_at: datetime
    updated_at: datetime
