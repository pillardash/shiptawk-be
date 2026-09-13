from asyncio import to_thread

from app.modules.achievement_digests.services.delivery import AchievementDigestDelivery
from app.services.email.base import EmailAddress, EmailMessage, EmailService
from app.services.email.rendering import render_email


class EmailServiceAchievementDigestSender:
    """Adapts digest delivery to the configured provider-neutral email service."""

    def __init__(self, email_service: EmailService) -> None:
        self.email_service = email_service

    async def send(self, delivery: AchievementDigestDelivery) -> str | None:
        content = render_email(
            subject=delivery.subject,
            preheader=delivery.body,
            heading=delivery.subject,
            body=delivery.body,
            action_label="View your dashboard",
            action_url=delivery.dashboard_url,
            footer=f"Unsubscribe: {delivery.unsubscribe_url}",
        )
        receipt = await to_thread(
            self.email_service.send,
            EmailMessage(
                to=[EmailAddress(email=delivery.recipient_email)],
                subject=content.subject,
                text=content.text,
                html=content.html,
            ),
        )
        return receipt.message_id if receipt is not None else None
