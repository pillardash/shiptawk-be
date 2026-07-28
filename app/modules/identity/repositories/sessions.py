from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models.oauth import AuthSession


async def get_session_by_refresh_hash(
    db: AsyncSession, refresh_token_hash: str, *, for_update: bool = False
) -> AuthSession | None:
    statement = select(AuthSession).where(AuthSession.refresh_token_hash == refresh_token_hash)
    if for_update:
        statement = statement.with_for_update()
    return (await db.scalars(statement)).first()


async def mark_session_family_compromised(
    db: AsyncSession, family_id: UUID, occurred_at: datetime
) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.family_id == family_id)
        .values(revoked_at=occurred_at, compromised_at=occurred_at)
    )


async def revoke_session_by_refresh_hash(
    db: AsyncSession, refresh_token_hash: str, revoked_at: datetime
) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.refresh_token_hash == refresh_token_hash)
        .values(revoked_at=revoked_at)
    )
