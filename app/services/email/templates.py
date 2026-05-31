from html import escape

from app.services.email.base import EmailContent


def email_verification_template(verification_url: str) -> EmailContent:
    escaped_url = escape(verification_url, quote=True)
    return EmailContent(
        subject="Verify your email address",
        text=(
            "Verify your email address by opening this link:\n\n"
            f"{verification_url}\n\n"
            "If you did not request this, you can ignore this email."
        ),
        html=(
            "<p>Verify your email address by opening this link:</p>"
            f'<p><a href="{escaped_url}">Verify email</a></p>'
            "<p>If you did not request this, you can ignore this email.</p>"
        ),
    )


def password_reset_template(reset_url: str) -> EmailContent:
    escaped_url = escape(reset_url, quote=True)
    return EmailContent(
        subject="Reset your password",
        text=(
            "Reset your password by opening this link:\n\n"
            f"{reset_url}\n\n"
            "If you did not request this, you can ignore this email."
        ),
        html=(
            "<p>Reset your password by opening this link:</p>"
            f'<p><a href="{escaped_url}">Reset password</a></p>'
            "<p>If you did not request this, you can ignore this email.</p>"
        ),
    )


def invite_user_template(invite_url: str) -> EmailContent:
    escaped_url = escape(invite_url, quote=True)
    return EmailContent(
        subject="You have been invited",
        text=f"Accept your invitation by opening this link:\n\n{invite_url}",
        html=f'<p>Accept your invitation:</p><p><a href="{escaped_url}">Accept invite</a></p>',
    )
