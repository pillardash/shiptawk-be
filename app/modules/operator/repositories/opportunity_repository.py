import hashlib
from typing import cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import Opportunity


async def acquire_identity_lock(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, identity: str
) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"{workspace_id}:{product_id}:{identity}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def list_identity_history(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, identity: str
) -> list[Opportunity]:
    return list(
        await db.scalars(
            select(Opportunity)
            .where(
                Opportunity.workspace_id == workspace_id,
                Opportunity.product_id == product_id,
                Opportunity.identity_fingerprint == identity,
            )
            .order_by(Opportunity.created_at, Opportunity.id)
        )
    )


async def get(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    opportunity_id: UUID,
    *,
    for_update: bool = False,
) -> Opportunity | None:
    statement = select(Opportunity).where(
        Opportunity.workspace_id == workspace_id,
        Opportunity.product_id == product_id,
        Opportunity.id == opportunity_id,
    )
    return cast(
        Opportunity | None,
        await db.scalar(statement.with_for_update() if for_update else statement),
    )


async def add(db: AsyncSession, opportunity: Opportunity) -> Opportunity:
    db.add(opportunity)
    await db.flush()
    return opportunity
