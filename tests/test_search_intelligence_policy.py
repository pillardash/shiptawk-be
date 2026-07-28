from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.modules.search_intelligence.enums.search_intelligence_enum import (
    SearchBaselineView,
    SearchPropertyCompatibility,
    SearchPropertyType,
)
from app.modules.search_intelligence.policies.search_baseline_policy import (
    aggregate_search_metrics,
    baseline_status,
    calculate_complete_periods,
    classify_query_baseline,
)
from app.modules.search_intelligence.policies.search_health_policy import search_health_state
from app.modules.search_intelligence.policies.search_property_policy import (
    classify_property_compatibility,
)


def test_complete_periods_are_adjacent_inclusive_28_day_windows() -> None:
    periods = calculate_complete_periods(date(2026, 7, 26))
    assert (periods.current.start, periods.current.end) == (date(2026, 6, 29), date(2026, 7, 26))
    assert (periods.prior.start, periods.prior.end) == (date(2026, 6, 1), date(2026, 6, 28))


def test_aggregate_metrics_uses_absolute_facts_and_weighted_position() -> None:
    result = aggregate_search_metrics([(3, 100, Decimal("4.0")), (2, 300, Decimal("8.0"))])
    assert (result.clicks, result.impressions) == (5, 400)
    assert result.ctr == Decimal("0.0125")
    assert result.average_position == Decimal("7.0")


def test_aggregate_metrics_handles_zero_impressions_without_inventing_rates() -> None:
    result = aggregate_search_metrics([(0, 0, None), (1, 0, Decimal("3"))])
    assert result.ctr is None
    assert result.average_position is None


def test_baseline_status_requires_each_site_total_day_and_accepts_zero_data() -> None:
    period = calculate_complete_periods(date(2026, 7, 26)).current
    all_dates = {period.start + timedelta(days=offset) for offset in range(28)}
    assert baseline_status(period, set()) == ("pending", tuple(sorted(all_dates)))
    assert baseline_status(period, all_dates - {date(2026, 7, 10)}) == (
        "partial",
        (date(2026, 7, 10),),
    )
    assert baseline_status(period, all_dates) == ("ready", ())


@pytest.mark.parametrize(
    ("expected", "kwargs"),
    [
        ("not_connected", {}),
        ("awaiting_property", {"connection_status": "active"}),
        (
            "syncing",
            {"connection_status": "active", "source_status": "active", "active_run": True},
        ),
        (
            "no_data",
            {
                "connection_status": "active",
                "source_status": "active",
                "successful_sync": True,
                "successful_zero_rows": True,
                "data_through_date": date(2026, 7, 24),
            },
        ),
        ("error", {"connection_status": "expired", "source_status": "active"}),
        (
            "healthy",
            {
                "connection_status": "active",
                "source_status": "active",
                "successful_sync": True,
                "data_through_date": date(2026, 7, 23),
                "expected_complete_date": date(2026, 7, 24),
            },
        ),
        (
            "stale",
            {
                "connection_status": "active",
                "source_status": "active",
                "successful_sync": True,
                "data_through_date": date(2026, 7, 20),
                "expected_complete_date": date(2026, 7, 24),
            },
        ),
        (
            "error",
            {
                "connection_status": "active",
                "source_status": "active",
                "last_run_status": "failed",
            },
        ),
        (
            "revoked",
            {
                "connection_status": "revoked",
                "source_status": "disconnected",
                "active_run": True,
            },
        ),
    ],
)
def test_health_state_transitions(expected: str, kwargs: dict[str, Any]) -> None:
    assert search_health_state(**kwargs) == expected


def test_baseline_v1_thresholds_are_conservative_and_descriptive() -> None:
    result = classify_query_baseline(
        current_clicks=1,
        previous_clicks=0,
        current_impressions=120,
        previous_impressions=0,
        current_position=Decimal("7"),
        has_confident_page=False,
    )
    assert result.policy_version == "baseline_v1"
    assert result.views == (
        SearchBaselineView.top_growing_queries,
        SearchBaselineView.queries_within_striking_distance,
        SearchBaselineView.high_impression_low_ctr,
        SearchBaselineView.newly_appearing_queries,
        SearchBaselineView.queries_without_confident_page,
    )


def test_baseline_v1_does_not_classify_below_thresholds() -> None:
    result = classify_query_baseline(
        current_clicks=0,
        previous_clicks=1,
        current_impressions=19,
        previous_impressions=80,
        current_position=Decimal("10"),
        has_confident_page=False,
    )
    assert result.views == ()


def test_baseline_growth_uses_click_direction_after_impression_eligibility() -> None:
    result = classify_query_baseline(
        current_clicks=5,
        previous_clicks=10,
        current_impressions=120,
        previous_impressions=100,
        current_position=Decimal("30"),
        has_confident_page=True,
    )
    assert result.views == (SearchBaselineView.top_declining_queries,)


def test_domain_property_accepts_subdomains_but_not_suffix_confusion() -> None:
    assert (
        classify_property_compatibility(
            property_type=SearchPropertyType.domain,
            provider_property_id="sc-domain:example.com",
            normalized_website_url="https://docs.example.com/",
        )
        is SearchPropertyCompatibility.compatible
    )
    assert (
        classify_property_compatibility(
            property_type=SearchPropertyType.domain,
            provider_property_id="sc-domain:example.com",
            normalized_website_url="https://notexample.com/",
        )
        is SearchPropertyCompatibility.incompatible
    )


def test_url_prefix_property_requires_scheme_host_and_path_boundary() -> None:
    assert (
        classify_property_compatibility(
            property_type=SearchPropertyType.url_prefix,
            provider_property_id="https://example.com/docs/",
            normalized_website_url="https://example.com/docs/start/",
        )
        is SearchPropertyCompatibility.compatible
    )
    assert (
        classify_property_compatibility(
            property_type=SearchPropertyType.url_prefix,
            provider_property_id="https://example.com/docs/",
            normalized_website_url="http://example.com/docs/start/",
        )
        is SearchPropertyCompatibility.incompatible
    )
    assert (
        classify_property_compatibility(
            property_type=SearchPropertyType.url_prefix,
            provider_property_id="https://example.com/docs/",
            normalized_website_url="https://example.com/docset/",
        )
        is SearchPropertyCompatibility.incompatible
    )
