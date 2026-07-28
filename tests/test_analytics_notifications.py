from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.modules.analytics.api import router as analytics_router
from app.modules.analytics.repositories.analytics import get_activity, get_summary
from app.modules.analytics.schemas import AnalyticsRate, StatusTimelinePoint
from app.modules.drafts.models import draft_status_events, drafts
from app.modules.identity.models.users import User
from app.modules.notifications.api import router as notifications_router
from app.modules.notifications.repositories.notifications import (
    disable_email_notifications,
    unread_count,
)
from app.modules.notifications.schemas import NotificationPreferencesUpdate
from app.modules.notifications.services.preferences import unsubscribe_user
from app.modules.repos.models import repos
from app.modules.workspaces.models import Workspace, WorkspaceMembership
from app.shared.exceptions import NotFoundError, ServiceUnavailableError


@pytest.fixture
def client_and_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, async_sessionmaker[AsyncSession]], None, None]:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-notification-signing-secret")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, sessions
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


async def seed_data(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[User, Workspace, Workspace]:
    async with sessions() as db:
        now = datetime.now(UTC)
        user = User(
            email="analytics@example.com",
            email_notifications_enabled=True,
            achievement_digest_enabled=True,
            achievement_digest_frequency="monthly",
            achievement_digest_prompt_dismissed=True,
            achievement_digest_onboarding_seen=True,
            last_dashboard_viewed_at=now - timedelta(days=2),
        )
        workspace = Workspace(name="Analytics")
        other_workspace = Workspace(name="Private")
        db.add_all([user, workspace, other_workspace])
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, is_active=True))

        repo_id = uuid4()
        other_repo_id = uuid4()
        await db.execute(
            insert(repos),
            [
                {
                    "id": repo_id,
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "repo_name": "app",
                    "repo_full_name": "safe/app",
                },
                {
                    "id": other_repo_id,
                    "workspace_id": other_workspace.id,
                    "user_id": user.id,
                    "repo_name": "secret",
                    "repo_full_name": "private/secret",
                },
            ],
        )
        draft_ids = [uuid4(), uuid4(), uuid4()]
        await db.execute(
            insert(drafts),
            [
                {
                    "id": draft_ids[0],
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "repo_id": repo_id,
                    "content": "private draft content",
                    "source_event_type": "push",
                    "source_event_payload": {"prompt": "must not leak"},
                    "status": "pending",
                    "approved_at": None,
                    "created_at": now - timedelta(days=1),
                },
                {
                    "id": draft_ids[1],
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "repo_id": repo_id,
                    "content": "approved content",
                    "source_event_type": "release",
                    "source_event_payload": {},
                    "status": "approved",
                    "approved_at": now,
                    "created_at": now - timedelta(days=3),
                },
                {
                    "id": draft_ids[2],
                    "workspace_id": other_workspace.id,
                    "user_id": user.id,
                    "repo_id": other_repo_id,
                    "content": "cross tenant content",
                    "source_event_type": "push",
                    "source_event_payload": {},
                    "status": "pending",
                    "approved_at": None,
                    "created_at": now,
                },
            ],
        )
        await db.execute(
            insert(draft_status_events),
            [
                {
                    "id": uuid4(),
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "repo_id": repo_id,
                    "draft_id": draft_ids[0],
                    "status": "pending",
                    "occurred_at": now - timedelta(days=1),
                },
                {
                    "id": uuid4(),
                    "workspace_id": other_workspace.id,
                    "user_id": user.id,
                    "repo_id": other_repo_id,
                    "draft_id": draft_ids[2],
                    "status": "pending",
                    "occurred_at": now,
                },
            ],
        )
        await db.commit()
        return user, workspace, other_workspace


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def test_analytics_summary_and_activity_are_tenant_safe_and_aggregate_only(
    client_and_sessions: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = client_and_sessions
    assert client.portal is not None
    user, workspace, other_workspace = client.portal.call(seed_data, sessions)

    summary = client.get(
        f"/api/v1/workspaces/{workspace.id}/analytics/summary?days=30", headers=auth(user)
    )
    activity = client.get(
        f"/api/v1/workspaces/{workspace.id}/analytics/activity?days=7", headers=auth(user)
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/analytics/summary", headers=auth(user)
    )

    assert summary.status_code == 200
    assert summary.json() == {
        "generatedDraftCount": 2,
        "approval": {"count": 1, "rate": 0.5},
    }
    assert "private draft content" not in summary.text
    assert "must not leak" not in summary.text
    assert activity.status_code == 200
    assert len(activity.json()["statusTimeline"]) == 7
    assert sum(point["pending"] for point in activity.json()["statusTimeline"]) == 1
    assert forbidden.status_code == 404


def test_unread_count_requires_membership_and_excludes_other_workspaces(
    client_and_sessions: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = client_and_sessions
    assert client.portal is not None
    user, workspace, other_workspace = client.portal.call(seed_data, sessions)

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/notifications/unread-count", headers=auth(user)
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/notifications/unread-count",
        headers=auth(user),
    )

    assert response.status_code == 200
    assert response.json() == {"count": 1}
    assert forbidden.status_code == 404


def test_notification_preferences_are_generated_safe_contracts(
    client_and_sessions: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = client_and_sessions
    assert client.portal is not None
    user, _, _ = client.portal.call(seed_data, sessions)

    response = client.get("/api/v1/notifications/preferences", headers=auth(user))
    updated = client.patch(
        "/api/v1/notifications/preferences",
        headers=auth(user),
        json={
            "emailNotificationsEnabled": False,
            "achievementDigestEnabled": False,
            "achievementDigestFrequency": "weekly",
            "achievementDigestPromptDismissed": False,
            "achievementDigestOnboardingSeen": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["emailNotificationsEnabled"] is True
    assert updated.status_code == 200
    assert updated.json() == {
        "emailNotificationsEnabled": False,
        "achievementDigestEnabled": False,
        "achievementDigestFrequency": "weekly",
        "achievementDigestPromptDismissed": False,
        "achievementDigestOnboardingSeen": False,
    }


def test_unsubscribe_rejects_invalid_tokens_and_disables_email(
    client_and_sessions: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = client_and_sessions
    assert client.portal is not None
    user, _, _ = client.portal.call(seed_data, sessions)
    import hashlib
    import hmac

    signature = hmac.new(
        b"test-notification-signing-secret", str(user.id).encode(), hashlib.sha256
    ).hexdigest()

    invalid = client.get("/api/v1/notifications/unsubscribe?token=invalid")
    response = client.get(f"/api/v1/notifications/unsubscribe?token={user.id}.{signature}")
    preferences = client.get("/api/v1/notifications/preferences", headers=auth(user))

    assert invalid.status_code == 400
    assert invalid.headers["content-type"].startswith("text/plain")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert preferences.json()["emailNotificationsEnabled"] is False


@pytest.mark.asyncio
async def test_analytics_repositories_cover_empty_and_aggregated_results() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    assert await get_summary(db, workspace_id, user_id, 30) is None
    assert await get_activity(db, workspace_id, user_id, 7) is None

    db.scalar.return_value = workspace_id
    summary_result = AsyncMock()
    summary_result.one = lambda: (4, 3)
    activity_result = AsyncMock()
    activity_result.all = lambda: [
        ("pending", datetime.now(UTC)),
        ("unknown", datetime.now(UTC)),
    ]
    db.execute.side_effect = [summary_result, activity_result]

    summary = await get_summary(db, workspace_id, user_id, 30)
    activity = await get_activity(db, workspace_id, user_id, 2)

    assert summary is not None
    assert summary[0] == 4
    assert summary[1].rate == 0.75
    assert activity is not None
    assert len(activity) == 2
    assert activity[-1].pending == 1


@pytest.mark.asyncio
async def test_notification_repository_branches() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    assert await unread_count(db, workspace_id, user_id, None) is None

    db.scalar.side_effect = [workspace_id, 2]
    assert await unread_count(db, workspace_id, user_id, None) == 2

    db.get.return_value = None
    assert await disable_email_notifications(db, user_id) is False
    user = User(email="disable@example.com", email_notifications_enabled=True)
    db.get.return_value = user
    assert await disable_email_notifications(db, user_id) is True
    assert user.email_notifications_enabled is False
    db.commit.assert_not_awaited()

    user.email_notifications_enabled = True
    assert await unsubscribe_user(db, user_id) is True
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_analytics_router_success_and_hidden_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(id=uuid4(), email="router@example.com")
    db = AsyncMock(spec=AsyncSession)
    workspace_id = uuid4()

    async def summary(*args: object) -> tuple[int, AnalyticsRate] | None:
        return 2, AnalyticsRate(count=1, rate=0.5)

    async def activity(*args: object) -> list[StatusTimelinePoint] | None:
        return [StatusTimelinePoint(date=datetime.now(UTC).date(), pending=1)]

    monkeypatch.setattr(analytics_router, "get_summary", summary)
    monkeypatch.setattr(analytics_router, "get_activity", activity)
    summary_response = await analytics_router.read_analytics_summary(workspace_id, user, db)
    assert summary_response.generated_draft_count == 2
    activity_response = await analytics_router.read_analytics_activity(workspace_id, user, db)
    assert len(activity_response.status_timeline) == 1

    async def missing(*args: object) -> None:
        return None

    monkeypatch.setattr(analytics_router, "get_summary", missing)
    monkeypatch.setattr(analytics_router, "get_activity", missing)
    with pytest.raises(NotFoundError):
        await analytics_router.read_analytics_summary(workspace_id, user, db)
    with pytest.raises(NotFoundError):
        await analytics_router.read_analytics_activity(workspace_id, user, db)


@pytest.mark.asyncio
async def test_notification_router_controlled_failure_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(
        id=uuid4(),
        email="notify-router@example.com",
        email_notifications_enabled=True,
        achievement_digest_enabled=False,
        achievement_digest_frequency="weekly",
        achievement_digest_prompt_dismissed=False,
        achievement_digest_onboarding_seen=False,
    )
    db = AsyncMock(spec=AsyncSession)
    workspace_id = uuid4()

    async def missing_count(*args: object) -> None:
        return None

    monkeypatch.setattr(notifications_router, "unread_count", missing_count)
    with pytest.raises(NotFoundError):
        await notifications_router.read_unread_count(workspace_id, user, db)

    monkeypatch.setattr(
        notifications_router,
        "get_settings",
        lambda: SimpleNamespace(token_encryption_key=None),
    )
    with pytest.raises(ServiceUnavailableError):
        await notifications_router.unsubscribe("token", db)

    secret = SimpleNamespace(get_secret_value=lambda: "secret")
    monkeypatch.setattr(
        notifications_router,
        "get_settings",
        lambda: SimpleNamespace(token_encryption_key=secret),
    )
    monkeypatch.setattr(notifications_router, "verify_unsubscribe_token", lambda *_: user.id)

    async def missing_user(*args: object) -> bool:
        return False

    monkeypatch.setattr(notifications_router, "unsubscribe_user", missing_user)
    response = await notifications_router.unsubscribe("token", db)
    assert response.status_code == 400

    preferences = notifications_router.read_notification_preferences(user)
    updated = await notifications_router.patch_notification_preferences(
        NotificationPreferencesUpdate(email_notifications_enabled=False), user, db
    )
    assert preferences.email_notifications_enabled is True
    assert updated.email_notifications_enabled is False
