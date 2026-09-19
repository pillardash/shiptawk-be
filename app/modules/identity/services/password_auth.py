import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password, hash_token, verify_password
from app.modules.identity.models.credentials import (
    EmailVerificationChallenge,
    PasswordCredential,
)
from app.modules.identity.models.oauth import AuthSession
from app.modules.identity.models.users import User
from app.modules.identity.services.sessions import create_refresh_session_record
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import Workspace, WorkspaceMembership
from app.modules.workspaces.repositories import get_first_active_membership
from app.services.email.base import EmailAddress, EmailMessage, create_email_service
from app.services.email.templates import email_verification_otp_template
from app.shared.exceptions import ConflictError, ServiceUnavailableError, UnauthorizedError


@dataclass(slots=True)
class PasswordSession:
    user: User
    session: AuthSession
    refresh_token: str


def _normalized_email(email: str) -> str:
    return email.strip().lower()


async def register_password_user(db: AsyncSession, *, name: str, email: str, password: str) -> None:
    normalized_email = _normalized_email(email)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"password-credential:{normalized_email}"},
        )
    existing = await db.scalar(
        select(PasswordCredential).where(PasswordCredential.email == normalized_email)
    )
    if existing is not None:
        raise ConflictError("An account already exists for this email.", code="email_registered")

    user = User(email=normalized_email, name=name, is_verified=False)
    db.add(user)
    await db.flush()
    workspace = Workspace(name=f"{name}'s workspace")
    db.add(workspace)
    await db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.owner,
        )
    )
    credential = PasswordCredential(
        user_id=user.id,
        email=normalized_email,
        password_hash=hash_password(password),
    )
    db.add(credential)
    await db.flush()
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        EmailVerificationChallenge(
            credential_id=credential.id,
            code_hash=hash_token(code),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            attempts_remaining=5,
        )
    )

    email_service = create_email_service(get_settings())
    if email_service is None:
        await db.rollback()
        raise ServiceUnavailableError(
            "Email verification is temporarily unavailable.", code="email_unavailable"
        )
    content = email_verification_otp_template(code)
    await asyncio.to_thread(
        email_service.send,
        EmailMessage(
            to=(EmailAddress(email=normalized_email, name=name),),
            subject=content.subject,
            text=content.text,
            html=content.html,
        ),
    )
    await db.commit()


async def verify_registration_code(db: AsyncSession, *, email: str, code: str) -> None:
    credential = await db.scalar(
        select(PasswordCredential)
        .where(PasswordCredential.email == _normalized_email(email))
        .with_for_update()
    )
    if credential is None:
        raise UnauthorizedError("Invalid or expired verification code.", code="invalid_otp")
    if credential.verified_at is not None:
        return
    challenge = await db.scalar(
        select(EmailVerificationChallenge)
        .where(
            EmailVerificationChallenge.credential_id == credential.id,
            EmailVerificationChallenge.consumed_at.is_(None),
        )
        .order_by(EmailVerificationChallenge.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    now = datetime.now(UTC)
    expires_at = challenge.expires_at if challenge else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        challenge is None
        or expires_at is None
        or expires_at <= now
        or challenge.attempts_remaining <= 0
    ):
        raise UnauthorizedError("Invalid or expired verification code.", code="invalid_otp")
    if not secrets.compare_digest(challenge.code_hash, hash_token(code)):
        challenge.attempts_remaining -= 1
        await db.commit()
        raise UnauthorizedError("Invalid or expired verification code.", code="invalid_otp")
    challenge.consumed_at = now
    credential.verified_at = now
    user = await db.get(User, credential.user_id)
    assert user is not None
    user.is_verified = True
    await db.commit()


async def login_with_password(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    user_agent: str | None,
    ip_address: str | None,
) -> PasswordSession:
    credential = await db.scalar(
        select(PasswordCredential).where(PasswordCredential.email == _normalized_email(email))
    )
    if credential is None or not verify_password(password, credential.password_hash):
        raise UnauthorizedError("Invalid email or password.", code="invalid_credentials")
    if credential.verified_at is None:
        raise UnauthorizedError("Verify your email before signing in.", code="email_not_verified")
    user = await db.get(User, credential.user_id)
    if user is None or user.deleted_at is not None or not user.is_active:
        raise UnauthorizedError("Invalid email or password.", code="invalid_credentials")
    membership = await get_first_active_membership(db, user.id)
    if membership is None:
        raise UnauthorizedError("Account has no active workspace.", code="workspace_access_denied")
    refresh_token, session = await create_refresh_session_record(
        db,
        user,
        current_workspace_id=membership.workspace_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return PasswordSession(user=user, session=session, refresh_token=refresh_token)
