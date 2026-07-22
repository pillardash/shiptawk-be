from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.legacy.models import drafts
from app.domains.users.models import User
from app.domains.workspaces.models import WorkspaceMembership


async def unread_count(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, viewed_at: object
) -> int | None:
    membership = await db.scalar(
        select(WorkspaceMembership.workspace_id).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.is_active.is_(True),
        )
    )
    if membership is None:
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
    await db.commit()
    return True
