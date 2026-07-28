from enum import StrEnum


class SearchProviderKey(StrEnum):
    google_search = "google_search"


class SearchPropertyType(StrEnum):
    domain = "domain"
    url_prefix = "url_prefix"


class SearchPropertyCompatibility(StrEnum):
    compatible = "compatible"
    incompatible = "incompatible"
    unknown = "unknown"


class SearchSourceStatus(StrEnum):
    active = "active"
    disconnected = "disconnected"


class SearchSyncTrigger(StrEnum):
    initial = "initial"
    scheduled = "scheduled"
    manual = "manual"


class SearchSyncStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class SearchMetricGrain(StrEnum):
    site_total = "site_total"
    query_page = "query_page"


class SearchPageMatchStatus(StrEnum):
    matched = "matched"
    unmatched = "unmatched"
    ambiguous = "ambiguous"


class SearchBaselineView(StrEnum):
    top_growing_queries = "top_growing_queries"
    top_declining_queries = "top_declining_queries"
    queries_within_striking_distance = "queries_within_striking_distance"
    high_impression_low_ctr = "high_impression_low_ctr"
    newly_appearing_queries = "newly_appearing_queries"
    queries_without_confident_page = "queries_without_confident_page"


__all__ = [
    "SearchBaselineView",
    "SearchMetricGrain",
    "SearchPageMatchStatus",
    "SearchPropertyCompatibility",
    "SearchPropertyType",
    "SearchProviderKey",
    "SearchSourceStatus",
    "SearchSyncStatus",
    "SearchSyncTrigger",
]
