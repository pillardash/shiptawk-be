from app.services.email.base import EmailContent
from app.services.email.rendering import render_email


def email_verification_template(verification_url: str) -> EmailContent:
    return render_email(
        subject="Verify your email address",
        preheader="Verify your email address to finish setting up your account.",
        heading="Verify your email address",
        body="Finish setting up your account by confirming your email address.",
        action_label="Verify email",
        action_url=verification_url,
        footer="If you did not request this, you can ignore this email.",
    )


def email_verification_otp_template(code: str) -> EmailContent:
    return EmailContent(
        subject="Your Shiptawk verification code",
        text=f"Your Shiptawk verification code is {code}. It expires in 10 minutes.",
        html=None,
    )


def password_reset_template(reset_url: str) -> EmailContent:
    return render_email(
        subject="Reset your password",
        preheader="Use this secure link to reset your password.",
        heading="Reset your password",
        body="We received a request to reset your password.",
        action_label="Reset password",
        action_url=reset_url,
        footer="If you did not request this, you can ignore this email.",
    )


def invite_user_template(invite_url: str) -> EmailContent:
    return render_email(
        subject="You have been invited",
        preheader="Accept your invitation to join the workspace.",
        heading="You have been invited",
        body="You have been invited to join a workspace.",
        action_label="Accept invite",
        action_url=invite_url,
    )
