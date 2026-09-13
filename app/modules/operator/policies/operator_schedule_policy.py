from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def operator_zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Operator timezone is invalid.") from exc


def next_operator_schedule(now: datetime, *, timezone: str, weekday: int, hour: int) -> datetime:
    zone = operator_zone(timezone)
    local_now = now.astimezone(zone)
    days = (weekday - local_now.weekday()) % 7
    candidate = datetime.combine(local_now.date() + timedelta(days=days), time(hour=hour), zone)
    if candidate <= local_now:
        candidate += timedelta(days=7)
    return candidate.astimezone(UTC)


def current_operator_period(now: datetime, *, timezone: str) -> tuple[datetime, datetime]:
    zone = operator_zone(timezone)
    local = now.astimezone(zone)
    local_start = datetime.combine(local.date() - timedelta(days=local.weekday()), time.min, zone)
    return local_start.astimezone(UTC), (local_start + timedelta(days=7)).astimezone(UTC)
