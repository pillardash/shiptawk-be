from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.modules.operator.policies.data_quality_policy import (
    quality_reasons,
    shipping_signal_reasons,
)
from app.modules.operator.schemas import DetectionInput, ShippingEventSnapshot


def make_input(**changes: object) -> DetectionInput:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    values = dict(
        schema_version="1",
        workspace_id=uuid4(),
        product_id=uuid4(),
        as_of=now,
        profile={"objective": "Grow", "profile_version": 3},
        search={
            "prior_start": date(2026, 6, 2),
            "prior_end": date(2026, 6, 29),
            "current_start": date(2026, 6, 30),
            "current_end": date(2026, 7, 27),
        },
        website={"profile_version": 3, "completed_at": now - timedelta(days=7)},
    )
    values.update(changes)
    return DetectionInput.model_validate(values)


def test_quality_accepts_exact_adjacent_windows_and_seven_day_crawl() -> None:
    assert quality_reasons(make_input(), requires_search=True, requires_website=True) == ()


def test_quality_does_not_require_optional_sources() -> None:
    assert quality_reasons(make_input(search=None, website=None)) == ()


def test_quality_rejects_gap_missing_dates_and_bad_search_state() -> None:
    data = make_input(
        search={
            "prior_start": date(2026, 6, 1),
            "prior_end": date(2026, 6, 28),
            "current_start": date(2026, 6, 30),
            "current_end": date(2026, 7, 27),
            "missing_dates": [date(2026, 7, 1)],
            "health_status": "degraded",
            "baseline_status": "building",
            "truncated": True,
        }
    )
    assert {r.value for r in quality_reasons(data, requires_search=True)} == {
        "search_unhealthy",
        "search_baseline_not_ready",
        "search_dates_incomplete",
        "search_truncated",
    }


def test_quality_rejects_naive_stale_future_and_profile_mismatch_crawls() -> None:
    data = make_input(website={"profile_version": 2, "completed_at": datetime(2026, 7, 1)})
    assert {r.value for r in quality_reasons(data, requires_website=True)} == {
        "website_stale",
        "website_profile_mismatch",
    }


def test_quality_rejects_incomplete_profile_failed_crawl_and_absent_dates() -> None:
    data = make_input(
        profile={"objective": "Grow", "status": "draft", "completeness": 99},
        search={},
        website={"status": "failed", "complete": False},
    )
    assert {
        r.value for r in quality_reasons(data, requires_search=True, requires_website=True)
    } == {
        "profile_not_approved",
        "profile_incomplete",
        "search_dates_incomplete",
        "website_crawl_failed",
        "website_stale",
    }


def test_shipping_signal_reports_every_failed_gate() -> None:
    event = ShippingEventSnapshot(
        event_key="old",
        occurred_at=datetime(2026, 6, 1, tzinfo=UTC),
        evaluation_outcome="ignore",
        public_worthiness_score=65,
        capability_coverage_score=".59",
        capability_relevance_score=".19",
    )
    assert {
        reason.value for reason in shipping_signal_reasons(event, datetime(2026, 7, 1, tzinfo=UTC))
    } == {
        "event_outside_window",
        "shipping_signal_ineligible",
        "event_not_public_worthy",
        "capability_match_insufficient",
    }
