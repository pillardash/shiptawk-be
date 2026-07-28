from datetime import UTC, date, datetime

from app.modules.search_intelligence.policies.search_sync_policy import search_sync_window


def test_initial_window_is_exactly_56_complete_days() -> None:
    window = search_sync_window(
        trigger="initial",
        today=date(2026, 7, 27),
        finalization_lag_days=3,
        selected_at=datetime(2026, 7, 27, tzinfo=UTC),
        latest_successful_data_date=None,
    )

    assert window == (date(2026, 5, 30), date(2026, 7, 24))
    assert (window[1] - window[0]).days + 1 == 56


def test_incremental_window_includes_seven_day_correction_overlap() -> None:
    window = search_sync_window(
        trigger="scheduled",
        today=date(2026, 8, 20),
        finalization_lag_days=3,
        selected_at=datetime(2026, 7, 27, tzinfo=UTC),
        latest_successful_data_date=date(2026, 8, 15),
    )

    assert window == (date(2026, 8, 9), date(2026, 8, 17))


def test_incremental_window_never_precedes_selection_and_can_be_empty() -> None:
    assert (
        search_sync_window(
            trigger="manual",
            today=date(2026, 7, 27),
            finalization_lag_days=3,
            selected_at=datetime(2026, 7, 27, tzinfo=UTC),
            latest_successful_data_date=None,
        )
        is None
    )
