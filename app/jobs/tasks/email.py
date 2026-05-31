import logging

from app.core.config import get_settings
from app.services.email.base import EmailAddress, EmailContent, EmailMessage, create_email_service
from app.services.email.templates import email_verification_template, password_reset_template

logger = logging.getLogger(__name__)


def send_password_reset_email(*, recipient_email: str, reset_url: str) -> None:
    content = password_reset_template(reset_url)
    send_templated_email(recipient_email=recipient_email, content=content)


def send_verification_email(*, recipient_email: str, verification_url: str) -> None:
    content = email_verification_template(verification_url)
    send_templated_email(recipient_email=recipient_email, content=content)


def send_invite_email(*, recipient_email: str, invite_url: str) -> None:
    logger.info("Invite email task received")


def send_system_notification_email(*, recipient_email: str, subject: str) -> None:
    logger.info("System notification email task received")


def send_templated_email(*, recipient_email: str, content: EmailContent) -> None:
    settings = get_settings()
    email_service = create_email_service(settings)
    if email_service is None:
        logger.info("Email job skipped because email provider is not configured")
        return
    email_service.send(
        EmailMessage(
            to=[EmailAddress(email=recipient_email)],
            subject=content.subject,
            text=content.text,
            html=content.html,
        )
    )
