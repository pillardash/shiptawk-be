from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import OpportunityFeedback, OpportunityStatusEvent


async def get_by_idempotency_key(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, key: str
) -> OpportunityFeedback | None:
    return cast(
        OpportunityFeedback | None,
        await db.scalar(
            select(OpportunityFeedback).where(
                OpportunityFeedback.workspace_id == workspace_id,
                OpportunityFeedback.product_id == product_id,
                OpportunityFeedback.idempotency_key == key,
            )
        ),
    )


async def add(db: AsyncSession, feedback: OpportunityFeedback) -> OpportunityFeedback:
    db.add(feedback)
    await db.flush()
    return feedback


async def add_status_event(
    db: AsyncSession, event: OpportunityStatusEvent
) -> OpportunityStatusEvent:
    db.add(event)
    await db.flush()
    return event
