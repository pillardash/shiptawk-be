from datetime import date

from app.modules.analytics.schemas.product_event_schema import (
    BrowserProductEventCommand,
    BrowserProductEventName,
    ProductEventName,
    ProductEventReceipt,
)
from app.shared.schemas import ApiSchema


class AnalyticsRate(ApiSchema):
    count: int
    rate: float


class AnalyticsSummaryResponse(ApiSchema):
    generated_draft_count: int
    approval: AnalyticsRate


class StatusTimelinePoint(ApiSchema):
    date: date
    pending: int = 0
    approved: int = 0
    rejected: int = 0
    posted: int = 0


class AnalyticsActivityResponse(ApiSchema):
    status_timeline: list[StatusTimelinePoint]


__all__ = [
    "AnalyticsActivityResponse",
    "AnalyticsRate",
    "AnalyticsSummaryResponse",
    "BrowserProductEventCommand",
    "BrowserProductEventName",
    "ProductEventName",
    "ProductEventReceipt",
    "StatusTimelinePoint",
]
