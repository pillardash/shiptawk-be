from enum import StrEnum


class WebsiteSourceStatus(StrEnum):
    active = "active"
    paused = "paused"
    disconnected = "disconnected"


class WebsiteCrawlTrigger(StrEnum):
    manual = "manual"
    scheduled = "scheduled"
    operator = "operator"


class WebsiteCrawlRunStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class WebsitePageStatus(StrEnum):
    active = "active"
    redirected = "redirected"
    blocked = "blocked"
    missing = "missing"
    failed = "failed"


class WebsitePageType(StrEnum):
    homepage = "homepage"
    product = "product"
    pricing = "pricing"
    conversion = "conversion"
    blog = "blog"
    documentation = "documentation"
    other = "other"


class WebsiteReadinessBlockingReason(StrEnum):
    not_configured = "website_not_configured"
    inactive = "website_inactive"
    initial_crawl_incomplete = "initial_crawl_incomplete"


class WebsiteCrawlResultStatus(StrEnum):
    succeeded = "succeeded"
    blocked = "blocked"
    failed = "failed"
    skipped = "skipped"


class WebsiteCapabilityMappingStatus(StrEnum):
    represented = "represented"
    partial = "partial"
    missing = "missing"


__all__ = [
    "WebsiteCapabilityMappingStatus",
    "WebsiteCrawlResultStatus",
    "WebsiteCrawlRunStatus",
    "WebsiteCrawlTrigger",
    "WebsitePageStatus",
    "WebsitePageType",
    "WebsiteReadinessBlockingReason",
    "WebsiteSourceStatus",
]
