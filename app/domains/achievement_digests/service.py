from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.achievement_digests.repository import (
    get_stored_digest_for_delivery,
    mark_digest_sent,
)
from app.domains.achievement_digests.schemas import AchievementDigestFrequency
from app.domains.notifications.service import unsubscribe_url


@dataclass(frozen=True)
class DigestPeriod:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class AchievementDigestDelivery:
    digest_id: UUID
    workspace_id: UUID
    recipient_email: str
    subject: str
    body: str
    dashboard_url: str
    unsubscribe_url: str


class AchievementDigestSender(Protocol):
    async def send(self, delivery: AchievementDigestDelivery) -> None: ...


def signed_unsubscribe_url(
    *, public_app_url: str, api_prefix: str, user_id: UUID, secret: str
) -> str:
    return unsubscribe_url(
        public_app_url=public_app_url,
        api_prefix=api_prefix,
        user_id=user_id,
        secret=secret,
    )


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


async def deliver_stored_digest(
    db: AsyncSession,
    workspace_id: UUID,
    digest_id: UUID,
    *,
    recipient_email: str,
    dashboard_url: str,
    unsubscribe_url: str,
    sender: AchievementDigestSender,
    sent_at: datetime | None = None,
) -> bool:
    digest = await get_stored_digest_for_delivery(db, workspace_id, digest_id)
    if digest is None or digest["sent_at"] is not None:
        return False
    delivery = AchievementDigestDelivery(
        digest_id=digest_id,
        workspace_id=workspace_id,
        recipient_email=recipient_email,
        subject=digest["subject"],
        body=digest["body"],
        dashboard_url=dashboard_url,
        unsubscribe_url=unsubscribe_url,
    )
    # The email provider has no delivery idempotency key. Persist the claim before
    # sending so an accepted send followed by a timeout cannot be delivered twice.
    claimed = await mark_digest_sent(db, workspace_id, digest_id, sent_at or datetime.now(UTC))
    if not claimed:
        return False
    await sender.send(delivery)
    return True
