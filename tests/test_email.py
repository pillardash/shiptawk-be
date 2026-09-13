import smtplib
from collections.abc import Callable
from email.message import EmailMessage as SmtpMessage
from types import TracebackType

import pytest

from app.core.config import Settings
from app.jobs.tasks import email as email_tasks
from app.services.email.base import (
    EmailAddress,
    EmailContent,
    EmailMessage,
    EmailSendKnownFailure,
    EmailSendOutcomeUnknown,
    EmailSendReceipt,
    create_email_service,
)
from app.services.email.smtp import SmtpEmailService, format_address
from app.services.email.templates import (
    email_verification_template,
    invite_user_template,
    password_reset_template,
)


def test_email_service_is_disabled_without_provider() -> None:
    settings = Settings()

    assert create_email_service(settings) is None


def test_smtp_provider_requires_host_and_from() -> None:
    with pytest.raises(ValueError):
        Settings(email_provider="smtp")


def test_smtp_credentials_must_be_configured_together() -> None:
    with pytest.raises(ValueError):
        Settings(
            email_provider="smtp",
            smtp_host="smtp.example.com",
            smtp_user="user",
            email_from="noreply@example.com",
        )


def test_smtp_provider_creates_smtp_service() -> None:
    settings = Settings(
        email_provider="smtp",
        smtp_host="smtp.example.com",
        smtp_port=2525,
        email_from="noreply@example.com",
    )

    service = create_email_service(settings)

    assert isinstance(service, SmtpEmailService)
    assert service.host == "smtp.example.com"
    assert service.port == 2525
    assert service.sender == "noreply@example.com"


def test_smtp_port_must_be_valid() -> None:
    with pytest.raises(ValueError):
        Settings(
            email_provider="smtp",
            smtp_host="smtp.example.com",
            smtp_port=70000,
            email_from="noreply@example.com",
        )


def test_format_address_supports_optional_name() -> None:
    assert format_address(EmailAddress(email="user@example.com")) == "user@example.com"
    assert (
        format_address(EmailAddress(email="user@example.com", name="Example User"))
        == "Example User <user@example.com>"
    )


def test_templates_include_urls() -> None:
    verification = email_verification_template("https://app.example.com/verify")
    reset = password_reset_template("https://app.example.com/reset")
    invite = invite_user_template("https://app.example.com/invite")

    assert "https://app.example.com/verify" in verification.text
    assert "https://app.example.com/reset" in reset.text
    assert "https://app.example.com/invite" in invite.text


def test_templates_escape_html_urls() -> None:
    content = password_reset_template('https://app.example.com/reset?token="x"&next=<script>')

    assert content.html is not None
    assert "&quot;x&quot;" in content.html
    assert "&lt;script&gt;" in content.html


def test_smtp_send_builds_message(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_messages: list[SmtpMessage] = []

    class FakeSmtp:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout
            self.started_tls = False
            self.login_args: tuple[str, str] | None = None

        def __enter__(self) -> "FakeSmtp":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        def starttls(self) -> None:
            self.started_tls = True

        def login(self, username: str, password: str) -> None:
            self.login_args = (username, password)

        def send_message(self, message: SmtpMessage) -> dict[str, tuple[int, bytes]]:
            sent_messages.append(message)
            return {}

    monkeypatch.setattr("app.services.email.smtp.smtplib.SMTP", FakeSmtp)
    service = SmtpEmailService(
        host="smtp.example.com",
        port=587,
        sender="noreply@example.com",
        username="user",
        password="pass",
    )

    receipt = service.send(
        EmailMessage(
            to=[EmailAddress(email="user@example.com", name="User")],
            subject="Subject",
            text="Text body",
            html="<p>HTML body</p>",
        )
    )

    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert message["From"] == "noreply@example.com"
    assert message["To"] == "User <user@example.com>"
    assert message["Subject"] == "Subject"
    assert isinstance(receipt, EmailSendReceipt)
    assert receipt.message_id == message["Message-ID"]
    assert receipt.accepted_recipients == ("user@example.com",)


def test_smtp_send_classifies_definitive_recipient_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectingSmtp:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            pass

        def __enter__(self) -> "RejectingSmtp":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        def starttls(self) -> None:
            pass

        def send_message(self, message: SmtpMessage) -> dict[str, tuple[int, bytes]]:
            raise smtplib.SMTPRecipientsRefused({"user@example.com": (550, b"mailbox unavailable")})

    monkeypatch.setattr("app.services.email.smtp.smtplib.SMTP", RejectingSmtp)
    service = SmtpEmailService(host="smtp.example.com", port=587, sender="from@example.com")

    with pytest.raises(EmailSendKnownFailure):
        service.send(
            EmailMessage(
                to=[EmailAddress(email="user@example.com")], subject="Subject", text="Body"
            )
        )


def test_smtp_send_classifies_disconnect_during_handoff_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DisconnectingSmtp:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            pass

        def __enter__(self) -> "DisconnectingSmtp":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        def starttls(self) -> None:
            pass

        def send_message(self, message: SmtpMessage) -> dict[str, tuple[int, bytes]]:
            raise smtplib.SMTPServerDisconnected("connection lost")

    monkeypatch.setattr("app.services.email.smtp.smtplib.SMTP", DisconnectingSmtp)
    service = SmtpEmailService(host="smtp.example.com", port=587, sender="from@example.com")

    with pytest.raises(EmailSendOutcomeUnknown):
        service.send(
            EmailMessage(
                to=[EmailAddress(email="user@example.com")], subject="Subject", text="Body"
            )
        )


def test_smtp_send_requires_recipient() -> None:
    service = SmtpEmailService(
        host="smtp.example.com",
        port=587,
        sender="noreply@example.com",
    )

    with pytest.raises(ValueError):
        service.send(EmailMessage(to=[], subject="Subject", text="Text body"))


@pytest.mark.parametrize(
    ("task", "url_argument", "subject"),
    [
        (email_tasks.send_password_reset_email, "reset_url", "Reset your password"),
        (email_tasks.send_verification_email, "verification_url", "Verify your email address"),
        (email_tasks.send_invite_email, "invite_url", "You have been invited"),
    ],
)
def test_email_jobs_render_and_send_shared_templates(
    monkeypatch: pytest.MonkeyPatch,
    task: Callable[..., None],
    url_argument: str,
    subject: str,
) -> None:
    sent: list[tuple[str, EmailContent]] = []
    monkeypatch.setattr(
        email_tasks,
        "send_templated_email",
        lambda *, recipient_email, content: sent.append((recipient_email, content)),
    )
    task(recipient_email="user@example.com", **{url_argument: "https://app.example.com/action"})
    assert sent[0][0] == "user@example.com"
    assert sent[0][1].subject == subject


def test_templated_email_skips_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_tasks, "get_settings", Settings)
    email_tasks.send_templated_email(
        recipient_email="user@example.com",
        content=password_reset_template("https://app.example.com/reset"),
    )


def test_templated_email_sends_through_configured_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[EmailMessage] = []
    service = type(
        "FakeEmailService", (), {"send": lambda self, message: messages.append(message)}
    )()
    monkeypatch.setattr(email_tasks, "get_settings", Settings)
    monkeypatch.setattr(email_tasks, "create_email_service", lambda settings: service)
    email_tasks.send_templated_email(
        recipient_email="user@example.com",
        content=password_reset_template("https://app.example.com/reset"),
    )
    assert messages[0].to == [EmailAddress(email="user@example.com")]
