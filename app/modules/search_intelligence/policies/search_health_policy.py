from datetime import date

STALE_AFTER_DAYS = 2


def search_health_state(
    *,
    connection_status: str | None = None,
    source_status: str | None = None,
    active_run: bool = False,
    last_run_status: str | None = None,
    successful_sync: bool = False,
    successful_zero_rows: bool = False,
    data_through_date: date | None = None,
    expected_complete_date: date | None = None,
) -> str:
    if connection_status == "revoked":
        return "revoked"
    if connection_status is None:
        return "not_connected"
    if connection_status in {"expired", "error"}:
        return "error"
    if source_status is None:
        return "awaiting_property"
    if active_run:
        return "syncing"
    if last_run_status == "failed":
        return "error"
    if successful_sync and (successful_zero_rows or data_through_date is None):
        return "no_data"
    if data_through_date is None or expected_complete_date is None:
        return "no_data"
    if (expected_complete_date - data_through_date).days > STALE_AFTER_DAYS:
        return "stale"
    return "healthy"


__all__ = ["STALE_AFTER_DAYS", "search_health_state"]
