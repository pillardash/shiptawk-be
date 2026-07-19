from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.domains.users.models import User
from app.domains.users.repository import create_user, get_user_by_email, get_user_by_id
from app.shared.exceptions import ConflictError, ForbiddenError, UnauthorizedError


async def register_user(db: AsyncSession, *, email: str, password: str) -> User:
    if await get_user_by_email(db, email) is not None:
        raise ConflictError("A user with this email already exists.", code="user_exists")
    try:
        return await create_user(db, email=email, password_hash=hash_password(password))
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("A user with this email already exists.", code="user_exists") from exc


async def authenticate_user(db: AsyncSession, *, email: str, password: str) -> User:
    user = await get_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Invalid email or password.", code="invalid_credentials")
    if not user.is_active:
        raise ForbiddenError("User account is inactive.", code="inactive_user")
    return user


async def get_active_user_by_id(db: AsyncSession, user_id: UUID) -> User:
    user = await get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("Invalid authentication credentials.", code="invalid_token")
    if not user.is_active:
        raise ForbiddenError("User account is inactive.", code="inactive_user")
    return user
