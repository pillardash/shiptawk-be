import smtplib
from email.message import EmailMessage as SmtpMessage
from email.utils import formataddr

from app.services.email.base import EmailAddress, EmailMessage


class SmtpEmailService:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        use_ssl: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.sender = sender
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.use_ssl = use_ssl

    def send(self, message: EmailMessage) -> None:
        if not message.to:
            raise ValueError("Email message must have at least one recipient.")
        smtp_message = SmtpMessage()
        smtp_message["From"] = self.sender
        smtp_message["To"] = ", ".join(format_address(address) for address in message.to)
        smtp_message["Subject"] = message.subject
        if message.reply_to:
            smtp_message["Reply-To"] = ", ".join(
                format_address(address) for address in message.reply_to
            )
        smtp_message.set_content(message.text)
        if message.html is not None:
            smtp_message.add_alternative(message.html, subtype="html")

        smtp_class = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        with smtp_class(self.host, self.port, timeout=10) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(smtp_message)


def format_address(address: EmailAddress) -> str:
    if address.name is None:
        return address.email
    return formataddr((address.name, address.email))
