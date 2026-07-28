from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.achievement_digests.policies.schedule import DigestPeriod, digest_period
from app.modules.achievement_digests.repositories.digests import (
    get_stored_digest_for_delivery,
    mark_digest_sent,
)
from app.modules.notifications.policies.unsubscribe import unsubscribe_url


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
    await db.commit()
    await sender.send(delivery)
    return True


__all__ = [
    "AchievementDigestDelivery",
    "AchievementDigestSender",
    "DigestPeriod",
    "deliver_stored_digest",
    "digest_period",
    "signed_unsubscribe_url",
]
