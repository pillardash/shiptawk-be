from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.drafts.models import drafts
from app.modules.identity.models.users import User
from app.modules.workspaces.repositories import has_active_membership


async def unread_count(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, viewed_at: object
) -> int | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None
    filters = [
        drafts.c.workspace_id == workspace_id,
        drafts.c.user_id == user_id,
        drafts.c.status == "pending",
    ]
    if viewed_at is not None:
        filters.append(drafts.c.created_at > viewed_at)
    return int(await db.scalar(select(func.count()).select_from(drafts).where(*filters)) or 0)


async def disable_email_notifications(db: AsyncSession, user_id: UUID) -> bool:
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        return False
    user.email_notifications_enabled = False
    return True
