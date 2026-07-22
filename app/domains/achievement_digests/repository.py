from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import distinct, exists, insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.achievement_digests.schemas import (
    AchievementDigestFeedbackAction,
    AchievementDigestFrequency,
)
from app.domains.legacy.models import achievement_digest_feedback, achievement_digests, repos
from app.domains.users.models import User
from app.domains.workspaces.models import WorkspaceMembership


@dataclass(frozen=True)
class DigestScheduleTarget:
    workspace_id: UUID
    user_id: UUID
    email: str
    user_name: str | None


async def has_active_membership(db: AsyncSession, workspace_id: UUID, user_id: UUID) -> bool:
    return (
        await db.scalar(
            select(WorkspaceMembership.workspace_id).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.is_active.is_(True),
            )
        )
        is not None
    )


async def list_recent_digests(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, limit: int
) -> list[dict[str, Any]] | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None
    rows = (
        await db.execute(
            select(achievement_digests)
            .where(
                achievement_digests.c.workspace_id == workspace_id,
                achievement_digests.c.user_id == user_id,
                achievement_digests.c.should_send.is_(True),
            )
            .order_by(achievement_digests.c.period_end.desc(), achievement_digests.c.id.desc())
            .limit(limit)
        )
    ).mappings()
    return [dict(row) for row in rows]


async def create_digest_feedback(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    digest_id: UUID,
    action: AchievementDigestFeedbackAction,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None
    digest = await db.scalar(
        select(achievement_digests.c.id).where(
            achievement_digests.c.id == digest_id,
            achievement_digests.c.workspace_id == workspace_id,
            achievement_digests.c.user_id == user_id,
        )
    )
    if digest is None:
        return None
    row = (
        (
            await db.execute(
                insert(achievement_digest_feedback)
                .values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    user_id=user_id,
                    digest_id=digest_id,
                    action=action.value,
                    metadata=metadata,
                )
                .returning(achievement_digest_feedback)
            )
        )
        .mappings()
        .one()
    )
    await db.commit()
    return dict(row)


async def list_digest_schedule_targets(
    db: AsyncSession, frequency: AchievementDigestFrequency
) -> list[DigestScheduleTarget]:
    rows = (
        await db.execute(
            select(
                distinct(WorkspaceMembership.workspace_id).label("workspace_id"),
                User.id.label("user_id"),
                User.email,
                User.github_username.label("user_name"),
            )
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.is_active.is_(True),
                User.is_active.is_(True),
                User.email.is_not(None),
                User.email_notifications_enabled.is_(True),
                User.achievement_digest_enabled.is_(True),
                User.achievement_digest_frequency == frequency.value,
                exists(
                    select(repos.c.id).where(
                        repos.c.workspace_id == WorkspaceMembership.workspace_id,
                        repos.c.user_id == User.id,
                    )
                ),
            )
            .order_by(WorkspaceMembership.workspace_id, User.id)
        )
    ).mappings()
    return [
        DigestScheduleTarget(
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            email=row["email"],
            user_name=row["user_name"],
        )
        for row in rows
    ]


async def get_stored_digest_for_delivery(
    db: AsyncSession, workspace_id: UUID, digest_id: UUID
) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                select(achievement_digests)
                .where(
                    achievement_digests.c.id == digest_id,
                    achievement_digests.c.workspace_id == workspace_id,
                    achievement_digests.c.should_send.is_(True),
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def mark_digest_sent(
    db: AsyncSession, workspace_id: UUID, digest_id: UUID, sent_at: datetime
) -> bool:
    result = await db.execute(
        update(achievement_digests)
        .where(
            achievement_digests.c.id == digest_id,
            achievement_digests.c.workspace_id == workspace_id,
            achievement_digests.c.sent_at.is_(None),
        )
        .values(sent_at=sent_at)
    )
    await db.commit()
    return cast(CursorResult[object], result).rowcount == 1
