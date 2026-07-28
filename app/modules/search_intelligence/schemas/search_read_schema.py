from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.shared.schemas import ApiSchema


class SearchPeriod(ApiSchema):
    start_date: date
    end_date: date


class SearchMetrics(ApiSchema):
    clicks: int
    impressions: int
    ctr: Decimal | None
    average_position: Decimal | None


class SearchComparisonItem(ApiSchema):
    id: UUID
    label: str
    current: SearchMetrics
    prior: SearchMetrics
    click_change: int
    impression_change: int


class SearchBaselineViews(ApiSchema):
    top_growing_queries: list[SearchComparisonItem]
    top_declining_queries: list[SearchComparisonItem]
    top_growing_pages: list[SearchComparisonItem]
    top_declining_pages: list[SearchComparisonItem]
    queries_within_striking_distance: list[SearchComparisonItem]
    high_impression_low_ctr: list[SearchComparisonItem]
    newly_appearing_queries: list[SearchComparisonItem]
    queries_without_relevant_page: list[SearchComparisonItem]


class SearchBaselineResponse(ApiSchema):
    status: Literal["pending", "partial", "ready", "no_data"]
    policy_version: str
    cutoff_date: date
    current_period: SearchPeriod
    prior_period: SearchPeriod
    current: SearchMetrics
    prior: SearchMetrics
    missing_dates: list[date]
    warnings: list[str]
    views: SearchBaselineViews


class SearchQueryItem(ApiSchema):
    id: UUID
    text: str
    metrics: SearchMetrics
    matched_page_status: Literal["matched", "unmatched", "ambiguous"]
    matched_page_count: int


class SearchPageItem(ApiSchema):
    id: UUID
    provider_url: str
    website_page_id: UUID | None
    match_status: str
    match_method: str | None
    metrics: SearchMetrics


class SearchQueryPageResponse(ApiSchema):
    start_date: date
    end_date: date
    total: int
    limit: int
    offset: int
    is_truncated: bool
    data_quality_warnings: list[str]
    items: list[SearchQueryItem]


class SearchPagePageResponse(ApiSchema):
    start_date: date
    end_date: date
    total: int
    limit: int
    offset: int
    is_truncated: bool
    data_quality_warnings: list[str]
    items: list[SearchPageItem]


class SearchHealthResponse(ApiSchema):
    state: Literal[
        "not_connected",
        "awaiting_property",
        "syncing",
        "healthy",
        "stale",
        "no_data",
        "error",
        "revoked",
    ]
    provider: str | None
    connection_status: str | None
    source_status: str | None
    property_id: UUID | None
    provider_property_id: str | None
    last_attempted_sync_at: datetime | None
    last_successful_sync_at: datetime | None
    data_through_date: date | None
    expected_complete_date: date | None
    freshness_lag_days: int | None
    active_run_status: str | None
    failure_category: str | None
    warnings: list[str]


__all__ = [name for name in globals() if name.startswith("Search")]
