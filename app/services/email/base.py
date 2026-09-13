from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import Settings


@dataclass(frozen=True)
class EmailAddress:
    email: str
    name: str | None = None


@dataclass(frozen=True)
class EmailContent:
    subject: str
    text: str
    html: str | None = None


@dataclass(frozen=True)
class EmailMessage:
    to: Sequence[EmailAddress]
    subject: str
    text: str
    html: str | None = None
    reply_to: Sequence[EmailAddress] = field(default_factory=tuple)


@dataclass(frozen=True)
class EmailSendReceipt:
    message_id: str
    accepted_recipients: tuple[str, ...]


class EmailSendKnownFailure(Exception):
    """The provider definitively did not accept the message."""


class EmailSendOutcomeUnknown(Exception):
    """The connection failed after handoff began, so delivery may have occurred."""


class EmailService(Protocol):
    def send(self, message: EmailMessage) -> EmailSendReceipt | None: ...


def create_email_service(settings: Settings) -> EmailService | None:
    if settings.email_provider is None:
        return None
    if settings.email_provider == "smtp":
        from app.services.email.smtp import SmtpEmailService

        if settings.smtp_host is None or settings.email_from is None:
            raise ValueError("SMTP email service requires SMTP_HOST and EMAIL_FROM.")
        return SmtpEmailService(
            host=settings.smtp_host,
            port=settings.smtp_port,
            sender=str(settings.email_from),
            username=settings.smtp_user,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            use_ssl=settings.smtp_use_ssl,
        )
    raise ValueError("Unsupported email provider.")
