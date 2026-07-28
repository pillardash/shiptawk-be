from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.modules.search_intelligence.enums.search_intelligence_enum import SearchBaselineView

BASELINE_POLICY_VERSION = "baseline_v1"


@dataclass(frozen=True, slots=True)
class DatePeriod:
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class ComparisonPeriods:
    current: DatePeriod
    prior: DatePeriod


@dataclass(frozen=True, slots=True)
class AggregatedSearchMetrics:
    clicks: int
    impressions: int
    ctr: Decimal | None
    average_position: Decimal | None


@dataclass(frozen=True, slots=True)
class QueryBaselineClassification:
    policy_version: str
    views: tuple[SearchBaselineView, ...]


def calculate_complete_periods(cutoff: date) -> ComparisonPeriods:
    current_start = cutoff - timedelta(days=27)
    return ComparisonPeriods(
        current=DatePeriod(current_start, cutoff),
        prior=DatePeriod(current_start - timedelta(days=28), current_start - timedelta(days=1)),
    )


def baseline_status(period: DatePeriod, observed_dates: set[date]) -> tuple[str, tuple[date, ...]]:
    expected = {
        period.start + timedelta(days=offset)
        for offset in range((period.end - period.start).days + 1)
    }
    missing = tuple(sorted(expected - observed_dates))
    if not observed_dates:
        return "pending", missing
    if missing:
        return "partial", missing
    return "ready", ()


def aggregate_search_metrics(
    facts: Iterable[tuple[int, int, Decimal | None]],
) -> AggregatedSearchMetrics:
    clicks = 0
    impressions = 0
    weighted_position = Decimal(0)
    positioned_impressions = 0
    for row_clicks, row_impressions, position in facts:
        if row_clicks < 0 or row_impressions < 0:
            raise ValueError("search facts must be nonnegative")
        clicks += row_clicks
        impressions += row_impressions
        if position is not None and row_impressions > 0:
            weighted_position += position * row_impressions
            positioned_impressions += row_impressions
    return AggregatedSearchMetrics(
        clicks=clicks,
        impressions=impressions,
        ctr=Decimal(clicks) / Decimal(impressions) if impressions else None,
        average_position=(
            weighted_position / Decimal(positioned_impressions) if positioned_impressions else None
        ),
    )


def classify_query_baseline(
    *,
    current_clicks: int,
    previous_clicks: int,
    current_impressions: int,
    previous_impressions: int,
    current_position: Decimal | None,
    has_confident_page: bool,
) -> QueryBaselineClassification:
    combined_impressions = current_impressions + previous_impressions
    ctr = Decimal(current_clicks) / Decimal(current_impressions) if current_impressions else None
    views: list[SearchBaselineView] = []
    if combined_impressions >= 100 and current_clicks > previous_clicks:
        views.append(SearchBaselineView.top_growing_queries)
    if combined_impressions >= 100 and current_clicks < previous_clicks:
        views.append(SearchBaselineView.top_declining_queries)
    if (
        current_impressions >= 100
        and current_position is not None
        and Decimal(4) <= current_position <= Decimal(20)
    ):
        views.append(SearchBaselineView.queries_within_striking_distance)
    if (
        current_impressions >= 100
        and current_position is not None
        and current_position <= Decimal(10)
        and ctr is not None
        and ctr < Decimal("0.02")
    ):
        views.append(SearchBaselineView.high_impression_low_ctr)
    if previous_impressions == 0 and current_impressions >= 20:
        views.append(SearchBaselineView.newly_appearing_queries)
    if current_impressions >= 20 and not has_confident_page:
        views.append(SearchBaselineView.queries_without_confident_page)
    return QueryBaselineClassification(BASELINE_POLICY_VERSION, tuple(views))


__all__ = [
    "BASELINE_POLICY_VERSION",
    "AggregatedSearchMetrics",
    "ComparisonPeriods",
    "DatePeriod",
    "QueryBaselineClassification",
    "aggregate_search_metrics",
    "baseline_status",
    "calculate_complete_periods",
    "classify_query_baseline",
]
