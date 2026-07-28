from asyncio import to_thread

from app.modules.achievement_digests.services.delivery import AchievementDigestDelivery
from app.services.email.base import EmailAddress, EmailMessage, EmailService


class EmailServiceAchievementDigestSender:
    """Adapts digest delivery to the configured provider-neutral email service."""

    def __init__(self, email_service: EmailService) -> None:
        self.email_service = email_service

    async def send(self, delivery: AchievementDigestDelivery) -> None:
        text = (
            f"{delivery.body}\n\n"
            f"View your dashboard: {delivery.dashboard_url}\n"
            f"Unsubscribe: {delivery.unsubscribe_url}"
        )
        await to_thread(
            self.email_service.send,
            EmailMessage(
                to=[EmailAddress(email=delivery.recipient_email)],
                subject=delivery.subject,
                text=text,
            ),
        )
