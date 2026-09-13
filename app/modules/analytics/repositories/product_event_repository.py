from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.models import ProductEvent


async def get_event_by_actor_key(
    db: AsyncSession, *, workspace_id: UUID, actor_id: UUID | None, idempotency_key: str
) -> ProductEvent | None:
    return cast(
        ProductEvent | None,
        await db.scalar(
            select(ProductEvent).where(
                ProductEvent.workspace_id == workspace_id,
                ProductEvent.actor_id == actor_id,
                ProductEvent.idempotency_key == idempotency_key,
            )
        ),
    )


async def add_event(db: AsyncSession, event: ProductEvent) -> ProductEvent:
    db.add(event)
    await db.flush()
    return event
