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
    "WebsiteSourceStatus",
]
