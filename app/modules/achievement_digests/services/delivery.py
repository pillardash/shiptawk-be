from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.achievement_digests.models import achievement_digests
from app.modules.achievement_digests.policies.schedule import DigestPeriod, digest_period
from app.modules.achievement_digests.repositories.digests import (
    get_stored_digest_for_delivery,
)
from app.modules.notifications.policies.unsubscribe import unsubscribe_url
from app.services.email.base import EmailSendOutcomeUnknown


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
    async def send(self, delivery: AchievementDigestDelivery) -> str | None: ...


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
) -> str:
    digest = await get_stored_digest_for_delivery(db, workspace_id, digest_id)
    if digest is None:
        return "suppressed"
    status = digest["delivery_status"]
    if digest["sent_at"] is not None or status in {"delivered", "unknown_outcome", "suppressed"}:
        return status or "delivered"
    if status == "pending":
        await db.execute(
            update(achievement_digests)
            .where(achievement_digests.c.id == digest_id)
            .values(delivery_status="unknown_outcome")
        )
        await db.commit()
        return "unknown_outcome"
    delivery = AchievementDigestDelivery(
        digest_id=digest_id,
        workspace_id=workspace_id,
        recipient_email=recipient_email,
        subject=digest["subject"],
        body=digest["body"],
        dashboard_url=dashboard_url,
        unsubscribe_url=unsubscribe_url,
    )
    await db.execute(
        update(achievement_digests)
        .where(
            achievement_digests.c.id == digest_id,
            achievement_digests.c.workspace_id == workspace_id,
        )
        .values(delivery_status="pending")
    )
    await db.commit()
    try:
        provider_delivery_id = await sender.send(delivery)
    except EmailSendOutcomeUnknown:
        await db.execute(
            update(achievement_digests)
            .where(achievement_digests.c.id == digest_id)
            .values(delivery_status="unknown_outcome")
        )
        await db.commit()
        return "unknown_outcome"
    except Exception:
        await db.execute(
            update(achievement_digests)
            .where(achievement_digests.c.id == digest_id)
            .values(delivery_status="failed")
        )
        await db.commit()
        raise
    delivered_at = sent_at or datetime.now(UTC)
    await db.execute(
        update(achievement_digests)
        .where(achievement_digests.c.id == digest_id)
        .values(
            delivery_status="delivered",
            provider_delivery_id=provider_delivery_id,
            sent_at=delivered_at,
        )
    )
    await db.commit()
    return "delivered"


__all__ = [
    "AchievementDigestDelivery",
    "AchievementDigestSender",
    "DigestPeriod",
    "deliver_stored_digest",
    "digest_period",
    "signed_unsubscribe_url",
]
