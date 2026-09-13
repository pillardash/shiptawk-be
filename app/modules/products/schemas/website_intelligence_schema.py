from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, computed_field, field_validator

from app.modules.products.enums.website_intelligence_enum import (
    WebsiteCapabilityMappingStatus,
    WebsiteCrawlResultStatus,
    WebsiteCrawlRunStatus,
    WebsiteCrawlTrigger,
    WebsitePageStatus,
    WebsitePageType,
    WebsiteReadinessBlockingReason,
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


class WebsiteCrawlDiscoveryDiagnostics(ApiSchema):
    discovery_method: Literal["links", "sitemap", "sitemap_and_links", "unknown"] = "unknown"
    sitemap_urls_attempted: int = 0
    sitemap_urls_succeeded: int = 0
    sitemap_pages_discovered: int = 0
    sitemap_fallback_used: bool = False


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

    @computed_field
    def discovery_diagnostics(self) -> WebsiteCrawlDiscoveryDiagnostics:
        summary = self.summary
        method = summary.get("discoveryMethod", "unknown")
        if method not in {"links", "sitemap", "sitemap_and_links"}:
            method = "unknown"
        return WebsiteCrawlDiscoveryDiagnostics(
            discovery_method=method,
            sitemap_urls_attempted=_nonnegative_int(summary.get("sitemapUrlsAttempted")),
            sitemap_urls_succeeded=_nonnegative_int(summary.get("sitemapUrlsSucceeded")),
            sitemap_pages_discovered=_nonnegative_int(summary.get("sitemapPagesDiscovered")),
            sitemap_fallback_used=summary.get("sitemapFallbackUsed") is True,
        )


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class WebsitePageResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    source_id: UUID
    first_discovered_run_id: UUID
    url: str
    canonical_url: str
    status: WebsitePageStatus
    crawl_run_id: UUID
    result_status: WebsiteCrawlResultStatus
    final_url: str | None
    http_status: int | None
    content_type: str | None
    title: str | None
    meta_description: str | None
    summary: str | None
    headings: list[str]
    indexable: bool | None
    indexability_reasons: list[str]
    fetched_at: datetime | None
    last_observed_at: datetime
    page_type: WebsitePageType
    is_key_page: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class WebsiteReadinessResponse(ApiSchema):
    configured: bool
    active: bool
    initial_crawl_complete: bool
    current_run_id: UUID | None
    current_run_status: WebsiteCrawlRunStatus | None
    blocking_reasons: list[WebsiteReadinessBlockingReason]
    latest_attempted_run_id: UUID | None
    latest_successful_run_id: UUID | None


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
