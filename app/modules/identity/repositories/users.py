from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models.users import User


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    statement = select(User).where(
        User.email == normalize_email(email),
        User.deleted_at.is_(None),
    )
    return (await db.scalars(statement)).first()


async def get_user_by_id(db: AsyncSession, user_id: UUID) -> User | None:
    statement = select(User).where(User.id == user_id, User.deleted_at.is_(None))
    return (await db.scalars(statement)).first()


async def create_user(db: AsyncSession, *, email: str | None) -> User:
    user = User(email=normalize_email(email) if email else None)
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user
