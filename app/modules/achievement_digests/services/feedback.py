from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.achievement_digests.enums import AchievementDigestFeedbackAction
from app.modules.achievement_digests.repositories.digests import create_digest_feedback


async def record_digest_feedback(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    digest_id: UUID,
    action: AchievementDigestFeedbackAction,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    row = await create_digest_feedback(db, workspace_id, user_id, digest_id, action, metadata)
    if row is not None:
        await db.commit()
    return row
