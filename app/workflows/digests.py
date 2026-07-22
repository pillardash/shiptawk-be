from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.achievement_digests.sender import EmailServiceAchievementDigestSender
from app.domains.achievement_digests.service import deliver_stored_digest, signed_unsubscribe_url
from app.domains.legacy.models import achievement_digests
from app.domains.users.models import User
from app.services.email.base import EmailService


@dataclass(frozen=True, slots=True)
class DigestWorkflowResult:
    sent: int
    skipped: int


class AchievementDigestWorkflow:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        email_service: EmailService,
        frontend_url: str,
        public_app_url: str,
        api_prefix: str,
        unsubscribe_secret: str,
    ) -> None:
        self._sessions = sessions
        self._sender = EmailServiceAchievementDigestSender(email_service)
        self._frontend_url = frontend_url
        self._public_app_url = public_app_url
        self._api_prefix = api_prefix
        self._unsubscribe_secret = unsubscribe_secret

    async def deliver(self, frequency: str) -> DigestWorkflowResult:
        async with self._sessions() as db:
            rows = (
                await db.execute(
                    select(
                        achievement_digests.c.id,
                        achievement_digests.c.workspace_id,
                        achievement_digests.c.user_id,
                        User.email,
                    )
                    .join(User, User.id == achievement_digests.c.user_id)
                    .where(
                        achievement_digests.c.frequency == frequency,
                        achievement_digests.c.should_send.is_(True),
                        achievement_digests.c.sent_at.is_(None),
                        User.is_active.is_(True),
                        User.email_notifications_enabled.is_(True),
                        User.email.is_not(None),
                    )
                    .order_by(achievement_digests.c.id)
                )
            ).all()
        sent = 0
        skipped = 0
        for row in rows:
            async with self._sessions() as db:
                delivered = await deliver_stored_digest(
                    db,
                    row.workspace_id,
                    row.id,
                    recipient_email=row.email,
                    dashboard_url=f"{self._frontend_url}/dashboard",
                    unsubscribe_url=signed_unsubscribe_url(
                        public_app_url=self._public_app_url,
                        api_prefix=self._api_prefix,
                        user_id=row.user_id,
                        secret=self._unsubscribe_secret,
                    ),
                    sender=self._sender,
                )
            if delivered:
                sent += 1
            else:
                skipped += 1
        return DigestWorkflowResult(sent=sent, skipped=skipped)
