from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.services.product_event_service import write_product_event
from app.modules.operator.models import RecommendationUsefulnessFeedback
from app.modules.operator.repositories.recommendation_feedback_repository import (
    replace_actor_feedback,
)


async def replace_recommendation_feedback(
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
    row = await replace_actor_feedback(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        recommendation_id=recommendation_id,
        actor_id=actor_id,
        rating=rating,
        reason=reason,
        comment=comment,
    )
    await write_product_event(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=actor_id,
        event_name="recommendation_feedback_submitted",
        idempotency_key=f"recommendation-feedback:{recommendation_id}:{row.updated_at.isoformat()}",
        resource_type="recommendation",
        resource_id=recommendation_id,
    )
    await db.commit()
    await db.refresh(row)
    return row
