from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rate_limit import RateLimitStore
from app.core.security import generate_token, hash_password, hash_token, verify_password
from app.domains.auth.models import AuthSession, AuthToken, AuthTokenPurpose
from app.domains.users.models import User
from app.domains.users.repository import get_user_by_email, get_user_by_id, normalize_email
from app.shared.exceptions import ForbiddenError, UnauthorizedError

MAX_USER_AGENT_LENGTH = 512
MAX_IP_ADDRESS_LENGTH = 45
MAX_DEVICE_NAME_LENGTH = 120


def truncate(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    return value[:max_length]


def _login_throttle_keys(*, email: str, ip_address: str | None) -> tuple[str, str]:
    return f"login:ip:{ip_address or 'unknown'}", f"login:email:{normalize_email(email)}"


def _password_reset_throttle_keys(*, token: str, ip_address: str | None) -> tuple[str, str]:
    return (
        f"password_reset:ip:{ip_address or 'unknown'}",
        f"password_reset:token:{hash_token(token)}",
    )


def check_login_throttle(
    *,
    store: RateLimitStore,
    email: str,
    ip_address: str | None,
) -> None:
    settings = get_settings()
    if not settings.login_throttle_enabled:
        return
    ip_key, email_key = _login_throttle_keys(email=email, ip_address=ip_address)
    ip_result = store.check(
        ip_key,
        limit=settings.login_throttle_ip_requests,
        window_seconds=settings.login_throttle_window_seconds,
    )
    email_result = store.check(
        email_key,
        limit=settings.login_throttle_email_requests,
        window_seconds=settings.login_throttle_window_seconds,
    )
    if not ip_result.allowed or not email_result.allowed:
        raise UnauthorizedError("Invalid email or password.", code="invalid_credentials")


def record_login_failure(
    *,
    store: RateLimitStore,
    email: str,
    ip_address: str | None,
) -> None:
    settings = get_settings()
    if not settings.login_throttle_enabled:
        return
    ip_key, email_key = _login_throttle_keys(email=email, ip_address=ip_address)
    store.hit(
        ip_key,
        limit=settings.login_throttle_ip_requests,
        window_seconds=settings.login_throttle_window_seconds,
    )
    store.hit(
        email_key,
        limit=settings.login_throttle_email_requests,
        window_seconds=settings.login_throttle_window_seconds,
    )


def check_password_reset_throttle(
    *,
    store: RateLimitStore,
    token: str,
    ip_address: str | None,
) -> None:
    settings = get_settings()
    ip_key, token_key = _password_reset_throttle_keys(token=token, ip_address=ip_address)
    ip_result = store.check(
        ip_key,
        limit=settings.password_reset_throttle_ip_requests,
        window_seconds=settings.password_reset_throttle_window_seconds,
    )
    token_result = store.check(
        token_key,
        limit=settings.password_reset_throttle_token_requests,
        window_seconds=settings.password_reset_throttle_window_seconds,
    )
    if not ip_result.allowed or not token_result.allowed:
        raise UnauthorizedError("Invalid or expired token.", code="invalid_token")


def record_password_reset_attempt(
    *,
    store: RateLimitStore,
    token: str,
    ip_address: str | None,
) -> None:
    settings = get_settings()
    ip_key, token_key = _password_reset_throttle_keys(token=token, ip_address=ip_address)
    store.hit(
        ip_key,
        limit=settings.password_reset_throttle_ip_requests,
        window_seconds=settings.password_reset_throttle_window_seconds,
    )
    store.hit(
        token_key,
        limit=settings.password_reset_throttle_token_requests,
        window_seconds=settings.password_reset_throttle_window_seconds,
    )


def auth_token_expiry_delta(purpose: AuthTokenPurpose) -> timedelta:
    settings = get_settings()
    match purpose:
        case AuthTokenPurpose.password_reset:
            return timedelta(minutes=settings.password_reset_token_expire_minutes)
        case AuthTokenPurpose.email_verification:
            return timedelta(hours=settings.email_verification_token_expire_hours)
        case _:
            raise ValueError(f"Unsupported auth token purpose: {purpose}")


async def create_refresh_session(
    db: AsyncSession,
    user: User,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
    device_name: str | None = None,
) -> str:
    settings = get_settings()
    token = generate_token()
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
        user_agent=truncate(user_agent, MAX_USER_AGENT_LENGTH),
        ip_address=truncate(ip_address, MAX_IP_ADDRESS_LENGTH),
        device_name=truncate(device_name, MAX_DEVICE_NAME_LENGTH),
    )
    db.add(session)
    await db.commit()
    return token


async def refresh_session(db: AsyncSession, refresh_token: str) -> User:
    now = datetime.now(UTC)
    user_id = (
        await db.execute(
            update(AuthSession)
            .where(
                AuthSession.refresh_token_hash == hash_token(refresh_token),
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
                AuthSession.deleted_at.is_(None),
            )
            .values(last_used_at=now, revoked_at=now)
            .returning(AuthSession.user_id)
        )
    ).scalar_one_or_none()
    if user_id is None:
        await db.rollback()
        raise UnauthorizedError("Invalid refresh token.", code="invalid_refresh_token")
    await db.commit()
    user = await get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("Invalid refresh token.", code="invalid_refresh_token")
    if not user.is_active:
        raise ForbiddenError("User account is inactive.", code="inactive_user")
    return user


async def revoke_refresh_session(db: AsyncSession, refresh_token: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.refresh_token_hash == hash_token(refresh_token))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()


async def revoke_user_sessions(db: AsyncSession, user: User) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()


async def change_password(
    db: AsyncSession, user: User, *, current_password: str, new_password: str
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise UnauthorizedError("Current password is incorrect.", code="invalid_current_password")
    user.password_hash = hash_password(new_password)
    await db.commit()
    await revoke_user_sessions(db, user)


async def create_auth_token(
    db: AsyncSession, *, email: str, purpose: AuthTokenPurpose
) -> str | None:
    normalized_email = normalize_email(email)
    expires_at = datetime.now(UTC) + auth_token_expiry_delta(purpose)
    user = await get_user_by_email(db, normalized_email)
    if user is None:
        return None
    token = generate_token()
    db.add(
        AuthToken(
            user_id=user.id,
            token_hash=hash_token(token),
            purpose=purpose,
            email=normalized_email,
            expires_at=expires_at,
        )
    )
    await db.commit()
    return token


async def consume_auth_token(db: AsyncSession, *, token: str, purpose: AuthTokenPurpose) -> User:
    now = datetime.now(UTC)
    user_id = (
        await db.execute(
            update(AuthToken)
            .where(
                AuthToken.token_hash == hash_token(token),
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
                AuthToken.revoked_at.is_(None),
                AuthToken.expires_at > now,
                AuthToken.deleted_at.is_(None),
                AuthToken.user_id.is_not(None),
            )
            .values(used_at=now)
            .returning(AuthToken.user_id)
        )
    ).scalar_one_or_none()
    if user_id is None:
        await db.rollback()
        raise UnauthorizedError("Invalid or expired token.", code="invalid_token")
    await db.commit()
    user = await get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("Invalid or expired token.", code="invalid_token")
    return user


async def reset_password(db: AsyncSession, *, token: str, new_password: str) -> None:
    user = await consume_auth_token(db, token=token, purpose=AuthTokenPurpose.password_reset)
    user.password_hash = hash_password(new_password)
    await db.commit()
    await revoke_user_sessions(db, user)


async def verify_email(db: AsyncSession, *, token: str) -> User:
    user = await consume_auth_token(db, token=token, purpose=AuthTokenPurpose.email_verification)
    user.is_verified = True
    await db.commit()
    await db.refresh(user)
    return user
