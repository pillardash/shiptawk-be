from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.modules.achievement_digests.enums import AchievementDigestFrequency


@dataclass(frozen=True)
class DigestPeriod:
    start: datetime
    end: datetime


def digest_period(frequency: AchievementDigestFrequency, now: datetime) -> DigestPeriod:
    end = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if frequency is AchievementDigestFrequency.weekly:
        return DigestPeriod(start=end - timedelta(days=7), end=end)
    previous_month = 12 if end.month == 1 else end.month - 1
    previous_year = end.year - 1 if end.month == 1 else end.year
    previous_day = min(end.day, monthrange(previous_year, previous_month)[1])
    return DigestPeriod(
        start=end.replace(year=previous_year, month=previous_month, day=previous_day), end=end
    )
