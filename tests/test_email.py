from email.message import EmailMessage as SmtpMessage
from types import TracebackType

import pytest

from app.core.config import Settings
from app.services.email.base import EmailAddress, EmailMessage, create_email_service
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

        def send_message(self, message: SmtpMessage) -> None:
            sent_messages.append(message)

    monkeypatch.setattr("app.services.email.smtp.smtplib.SMTP", FakeSmtp)
    service = SmtpEmailService(
        host="smtp.example.com",
        port=587,
        sender="noreply@example.com",
        username="user",
        password="pass",
    )

    service.send(
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


def test_smtp_send_requires_recipient() -> None:
    service = SmtpEmailService(
        host="smtp.example.com",
        port=587,
        sender="noreply@example.com",
    )

    with pytest.raises(ValueError):
        service.send(EmailMessage(to=[], subject="Subject", text="Text body"))
