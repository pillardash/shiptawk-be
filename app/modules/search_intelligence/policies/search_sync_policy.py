from datetime import date, datetime, timedelta
from typing import Literal

INITIAL_DAYS = 56
CORRECTION_OVERLAP_DAYS = 7
SYNC_POLICY_VERSION = "search-sync-v1"


def search_sync_window(
    *,
    trigger: Literal["initial", "scheduled", "manual"] | str,
    today: date,
    finalization_lag_days: int,
    selected_at: datetime,
    latest_successful_data_date: date | None,
) -> tuple[date, date] | None:
    if finalization_lag_days < 0:
        raise ValueError("finalization lag must not be negative")
    cutoff = today - timedelta(days=finalization_lag_days)
    if trigger == "initial":
        return cutoff - timedelta(days=INITIAL_DAYS - 1), cutoff
    if trigger not in {"manual", "scheduled"}:
        raise ValueError("unsupported search sync trigger")

    selected_date = selected_at.date()
    start = selected_date
    if latest_successful_data_date is not None:
        start = max(
            selected_date,
            latest_successful_data_date - timedelta(days=CORRECTION_OVERLAP_DAYS - 1),
        )
    return None if start > cutoff else (start, cutoff)


__all__ = ["SYNC_POLICY_VERSION", "search_sync_window"]
