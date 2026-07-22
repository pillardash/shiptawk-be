import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import generate_token, hash_token
from app.domains.auth.models import AuthSession
from app.domains.users.models import User
from app.domains.users.repository import get_user_by_id
from app.shared.exceptions import UnauthorizedError

MAX_USER_AGENT_LENGTH = 512
MAX_IP_ADDRESS_LENGTH = 45
MAX_DEVICE_NAME_LENGTH = 120


def truncate(value: str | None, max_length: int) -> str | None:
    return value[:max_length] if value is not None else None


async def create_refresh_session_record(
    db: AsyncSession,
    user: User,
    *,
    current_workspace_id: uuid.UUID,
    user_agent: str | None = None,
    ip_address: str | None = None,
    device_name: str | None = None,
    family_id: uuid.UUID | None = None,
    generation: int = 0,
    commit: bool = True,
) -> tuple[str, AuthSession]:
    token = generate_token()
    session = AuthSession(
        user_id=user.id,
        current_workspace_id=current_workspace_id,
        refresh_token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().refresh_token_expire_days),
        user_agent=truncate(user_agent, MAX_USER_AGENT_LENGTH),
        ip_address=truncate(ip_address, MAX_IP_ADDRESS_LENGTH),
        device_name=truncate(device_name, MAX_DEVICE_NAME_LENGTH),
        family_id=family_id or uuid.uuid4(),
        generation=generation,
    )
    db.add(session)
    if commit:
        await db.commit()
        await db.refresh(session)
    else:
        await db.flush()
    return token, session


async def rotate_refresh_session(
    db: AsyncSession,
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
    device_name: str | None = None,
) -> tuple[User, str, AuthSession]:
    now = datetime.now(UTC)
    existing = (
        await db.scalars(
            select(AuthSession)
            .where(AuthSession.refresh_token_hash == hash_token(refresh_token))
            .with_for_update()
        )
    ).first()
    expires_at = existing.expires_at if existing else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if existing is None or existing.deleted_at is not None or not expires_at or expires_at <= now:
        await db.rollback()
        raise UnauthorizedError("Invalid refresh token.", code="invalid_refresh_token")
    if existing.revoked_at is not None or existing.replaced_by_id is not None:
        await db.execute(
            update(AuthSession)
            .where(AuthSession.family_id == existing.family_id)
            .values(revoked_at=now, compromised_at=now)
        )
        await db.commit()
        raise UnauthorizedError("Refresh token replay detected.", code="refresh_token_reuse")

    user = await get_user_by_id(db, existing.user_id)
    if user is None:
        raise UnauthorizedError("Invalid refresh token.", code="invalid_refresh_token")
    if not user.is_active:
        raise UnauthorizedError("User account is inactive.", code="inactive_user")
    replacement_token, replacement = await create_refresh_session_record(
        db,
        user,
        current_workspace_id=existing.current_workspace_id,
        user_agent=user_agent,
        ip_address=ip_address,
        device_name=device_name,
        family_id=existing.family_id,
        generation=existing.generation + 1,
        commit=False,
    )
    existing.last_used_at = now
    existing.revoked_at = now
    existing.replaced_by_id = replacement.id
    await db.commit()
    return user, replacement_token, replacement


async def get_refresh_session(db: AsyncSession, refresh_token: str) -> AuthSession | None:
    return (
        await db.scalars(
            select(AuthSession).where(AuthSession.refresh_token_hash == hash_token(refresh_token))
        )
    ).first()


def refresh_session_is_active(session: AuthSession | None) -> bool:
    if session is None or session.deleted_at is not None or session.revoked_at is not None:
        return False
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > datetime.now(UTC) and session.replaced_by_id is None


async def revoke_refresh_session(db: AsyncSession, refresh_token: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.refresh_token_hash == hash_token(refresh_token))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
