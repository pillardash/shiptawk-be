from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import RecommendationUsefulnessFeedback


async def get_actor_feedback(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    recommendation_id: UUID,
    actor_id: UUID,
) -> RecommendationUsefulnessFeedback | None:
    return cast(
        RecommendationUsefulnessFeedback | None,
        await db.scalar(
            select(RecommendationUsefulnessFeedback).where(
                RecommendationUsefulnessFeedback.workspace_id == workspace_id,
                RecommendationUsefulnessFeedback.product_id == product_id,
                RecommendationUsefulnessFeedback.recommendation_id == recommendation_id,
                RecommendationUsefulnessFeedback.actor_id == actor_id,
            )
        ),
    )


async def replace_actor_feedback(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    recommendation_id: UUID,
    actor_id: UUID,
    rating: str,
    reason: str | None,
    comment: str | None,
) -> RecommendationUsefulnessFeedback:
    row = await get_actor_feedback(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        recommendation_id=recommendation_id,
        actor_id=actor_id,
    )
    if row is None:
        row = RecommendationUsefulnessFeedback(
            workspace_id=workspace_id,
            product_id=product_id,
            recommendation_id=recommendation_id,
            actor_id=actor_id,
            rating=rating,
            reason=reason,
            comment=comment,
        )
        db.add(row)
    else:
        row.rating, row.reason, row.comment = rating, reason, comment
    await db.flush()
    return row
