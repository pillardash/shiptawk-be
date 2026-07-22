from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.analytics.schemas import AnalyticsRate, StatusTimelinePoint
from app.domains.legacy.models import draft_status_events, drafts
from app.domains.workspaces.models import WorkspaceMembership


async def has_active_membership(db: AsyncSession, workspace_id: UUID, user_id: UUID) -> bool:
    membership = await db.scalar(
        select(WorkspaceMembership.workspace_id).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.is_active.is_(True),
        )
    )
    return membership is not None


async def get_summary(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, days: int
) -> tuple[int, AnalyticsRate] | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None
    since = datetime.now(UTC) - timedelta(days=days)
    row = (
        await db.execute(
            select(
                func.count(drafts.c.id),
                func.count(drafts.c.id).filter(drafts.c.status.in_(("approved", "posted"))),
            ).where(drafts.c.workspace_id == workspace_id, drafts.c.created_at >= since)
        )
    ).one()
    generated = int(row[0] or 0)
    approved = int(row[1] or 0)
    return generated, AnalyticsRate(
        count=approved,
        rate=round(approved / generated, 4) if generated else 0,
    )


async def get_activity(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, days: int
) -> list[StatusTimelinePoint] | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None
    today = datetime.now(UTC).date()
    first_day = today - timedelta(days=days - 1)
    since = datetime.combine(first_day, datetime.min.time(), tzinfo=UTC)
    rows = (
        await db.execute(
            select(draft_status_events.c.status, draft_status_events.c.occurred_at).where(
                draft_status_events.c.workspace_id == workspace_id,
                draft_status_events.c.occurred_at >= since,
            )
        )
    ).all()
    counts: dict[date, dict[str, int]] = {
        first_day + timedelta(days=offset): {
            "pending": 0,
            "approved": 0,
            "rejected": 0,
            "posted": 0,
        }
        for offset in range(days)
    }
    for status, occurred_at in rows:
        event_date = occurred_at.date()
        if event_date in counts and status in counts[event_date]:
            counts[event_date][status] += 1
    return [StatusTimelinePoint(date=day, **values) for day, values in counts.items()]
