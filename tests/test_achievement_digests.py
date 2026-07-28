from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.modules.achievement_digests.enums import AchievementDigestFrequency
from app.modules.achievement_digests.models import achievement_digest_feedback, achievement_digests
from app.modules.achievement_digests.policies.schedule import digest_period
from app.modules.achievement_digests.providers.email import EmailServiceAchievementDigestSender
from app.modules.achievement_digests.repositories.digests import (
    DigestScheduleTarget,
    list_digest_schedule_targets,
)
from app.modules.achievement_digests.services.delivery import (
    AchievementDigestDelivery,
    deliver_stored_digest,
    signed_unsubscribe_url,
)
from app.modules.identity.models.users import User
from app.modules.repos.models import repos
from app.modules.workspaces.models import Workspace, WorkspaceMembership
from app.services.email.base import EmailMessage
from app.workflows.digests import AchievementDigestWorkflow


def auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.fixture
def digest_client() -> Generator[tuple[TestClient, async_sessionmaker[AsyncSession]], None, None]:
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


async def seed_digests(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[User, Workspace, Workspace, list[UUID]]:
    async with sessions() as db:
        user = User(
            email="digest@example.com",
            achievement_digest_enabled=True,
            achievement_digest_frequency="weekly",
            email_notifications_enabled=True,
        )
        workspace = Workspace(name="Digest workspace")
        other_workspace = Workspace(name="Other workspace")
        db.add_all([user, workspace, other_workspace])
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, is_active=True))
        await db.execute(
            insert(repos).values(
                id=uuid4(),
                workspace_id=workspace.id,
                user_id=user.id,
                repo_name="app",
                repo_full_name="example/app",
            )
        )
        digest_ids = [uuid4(), uuid4(), uuid4()]
        base = {
            "user_id": user.id,
            "frequency": "weekly",
            "period_start": datetime(2026, 7, 1, tzinfo=UTC),
            "subject": "Weekly wins",
            "body": "A useful summary",
            "digest_json": {"products": []},
            "product_id": None,
            "quality_score": None,
            "recommended_post": None,
            "event_ids": [],
            "draft_ids": [],
            "repo_ids": [],
            "sent_at": None,
        }
        await db.execute(
            insert(achievement_digests),
            [
                {
                    **base,
                    "id": digest_ids[0],
                    "workspace_id": workspace.id,
                    "period_end": datetime(2026, 7, 8, tzinfo=UTC),
                    "should_send": True,
                    "quality_score": 88,
                },
                {
                    **base,
                    "id": digest_ids[1],
                    "workspace_id": workspace.id,
                    "period_end": datetime(2026, 7, 15, tzinfo=UTC),
                    "should_send": False,
                },
                {
                    **base,
                    "id": digest_ids[2],
                    "workspace_id": other_workspace.id,
                    "period_end": datetime(2026, 7, 22, tzinfo=UTC),
                    "should_send": True,
                },
            ],
        )
        await db.commit()
        return user, workspace, other_workspace, digest_ids


def test_list_digests_is_workspace_scoped_sendable_and_camel_case(
    digest_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = digest_client
    assert client.portal is not None
    user, workspace, _, digest_ids = client.portal.call(seed_digests, sessions)

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/achievement-digests?limit=20",
        headers=auth_headers(user),
    )

    assert response.status_code == 200
    digest = response.json()["data"][0]
    assert digest == {
        "id": str(digest_ids[0]),
        "workspaceId": str(workspace.id),
        "productId": None,
        "frequency": "weekly",
        "periodStart": digest["periodStart"],
        "periodEnd": digest["periodEnd"],
        "subject": "Weekly wins",
        "body": "A useful summary",
        "digestJson": {"products": []},
        "qualityScore": 88,
        "shouldSend": True,
        "recommendedPost": None,
        "eventIds": [],
        "draftIds": [],
        "repoIds": [],
        "sentAt": None,
        "createdAt": digest["createdAt"],
    }
    assert digest["periodStart"].startswith("2026-07-01T00:00:00")
    assert digest["periodEnd"].startswith("2026-07-08T00:00:00")
    assert "userId" not in digest


def test_list_digests_hides_workspace_without_membership(
    digest_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = digest_client
    assert client.portal is not None
    user, _, other_workspace, _ = client.portal.call(seed_digests, sessions)

    response = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/achievement-digests",
        headers=auth_headers(user),
    )

    assert response.status_code == 404


def test_feedback_is_written_with_server_resolved_tenant_and_actor(
    digest_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = digest_client
    assert client.portal is not None
    user, workspace, _, digest_ids = client.portal.call(seed_digests, sessions)

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/achievement-digests/{digest_ids[0]}/feedback",
        headers=auth_headers(user),
        json={"action": "useful", "metadata": {"surface": "dashboard"}},
    )

    assert response.status_code == 201
    assert response.json()["workspaceId"] == str(workspace.id)
    assert response.json()["action"] == "useful"
    assert "userId" not in response.json()

    async def stored_feedback() -> dict[str, object]:
        async with sessions() as db:
            row = (await db.execute(select(achievement_digest_feedback))).mappings().one()
            return dict(row)

    stored = client.portal.call(stored_feedback)
    assert stored["workspace_id"] == workspace.id
    assert stored["user_id"] == user.id


def test_feedback_cannot_target_digest_from_another_workspace(
    digest_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = digest_client
    assert client.portal is not None
    user, workspace, _, digest_ids = client.portal.call(seed_digests, sessions)

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/achievement-digests/{digest_ids[2]}/feedback",
        headers=auth_headers(user),
        json={"action": "copied"},
    )

    assert response.status_code == 404


def test_feedback_rejects_oversized_metadata(
    digest_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = digest_client
    assert client.portal is not None
    user, workspace, _, digest_ids = client.portal.call(seed_digests, sessions)

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/achievement-digests/{digest_ids[0]}/feedback",
        headers=auth_headers(user),
        json={"action": "useful", "metadata": {"note": "x" * 2001}},
    )

    assert response.status_code == 422


def test_digest_periods_are_utc_and_half_open() -> None:
    weekly = digest_period(AchievementDigestFrequency.weekly, datetime(2026, 7, 22, 15, tzinfo=UTC))
    monthly = digest_period(
        AchievementDigestFrequency.monthly, datetime(2026, 3, 1, 15, tzinfo=UTC)
    )

    assert weekly.start == datetime(2026, 7, 15, tzinfo=UTC)
    assert weekly.end == datetime(2026, 7, 22, tzinfo=UTC)
    assert monthly.start == datetime(2026, 2, 1, tzinfo=UTC)
    assert monthly.end == datetime(2026, 3, 1, tzinfo=UTC)
    january = digest_period(
        AchievementDigestFrequency.monthly, datetime(2026, 1, 31, 15, tzinfo=UTC)
    )
    assert january.start == datetime(2025, 12, 31, tzinfo=UTC)


def test_schedule_targets_require_preferences_active_membership_and_workspace_activity(
    digest_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = digest_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_digests, sessions)

    async def targets() -> list[DigestScheduleTarget]:
        async with sessions() as db:
            return await list_digest_schedule_targets(db, AchievementDigestFrequency.weekly)

    result = client.portal.call(targets)

    assert [(item.user_id, item.workspace_id, item.email) for item in result] == [
        (user.id, workspace.id, "digest@example.com")
    ]


async def test_delivery_marks_sent_only_after_provider_neutral_sender_succeeds(
    digest_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = digest_client
    assert client.portal is not None
    _, workspace, _, digest_ids = client.portal.call(seed_digests, sessions)
    deliveries: list[AchievementDigestDelivery] = []

    class FakeSender:
        async def send(self, delivery: AchievementDigestDelivery) -> None:
            deliveries.append(delivery)

    async with sessions() as db:
        sent = await deliver_stored_digest(
            db,
            workspace.id,
            digest_ids[0],
            recipient_email="digest@example.com",
            dashboard_url="https://app.example.test/dashboard",
            unsubscribe_url="https://app.example.test/unsubscribe/token",
            sender=FakeSender(),
            sent_at=datetime(2026, 7, 22, 16, tzinfo=UTC),
        )

    assert sent is True
    assert deliveries[0].subject == "Weekly wins"
    async with sessions() as db:
        stored_sent_at = await db.scalar(
            select(achievement_digests.c.sent_at).where(achievement_digests.c.id == digest_ids[0])
        )
    assert stored_sent_at is not None
    assert stored_sent_at.replace(tzinfo=UTC) == datetime(2026, 7, 22, 16, tzinfo=UTC)

    async with sessions() as db:
        sent_again = await deliver_stored_digest(
            db,
            workspace.id,
            digest_ids[0],
            recipient_email="digest@example.com",
            dashboard_url="https://app.example.test/dashboard",
            unsubscribe_url="https://app.example.test/unsubscribe/token",
            sender=FakeSender(),
        )
    assert sent_again is False
    assert len(deliveries) == 1


async def test_delivery_is_recorded_before_send_so_retries_cannot_duplicate_an_accepted_email(
    digest_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = digest_client
    assert client.portal is not None
    _, workspace, _, digest_ids = client.portal.call(seed_digests, sessions)

    class UncertainSender:
        async def send(self, delivery: AchievementDigestDelivery) -> None:
            raise RuntimeError("provider accepted email but connection closed")

    async with sessions() as db:
        with pytest.raises(RuntimeError, match="provider accepted email but connection closed"):
            await deliver_stored_digest(
                db,
                workspace.id,
                digest_ids[0],
                recipient_email="digest@example.com",
                dashboard_url="https://app.example.test/dashboard",
                unsubscribe_url="https://app.example.test/unsubscribe/token",
                sender=UncertainSender(),
            )

    async with sessions() as db:
        sent_again = await deliver_stored_digest(
            db,
            workspace.id,
            digest_ids[0],
            recipient_email="digest@example.com",
            dashboard_url="https://app.example.test/dashboard",
            unsubscribe_url="https://app.example.test/unsubscribe/token",
            sender=UncertainSender(),
        )
        stored_sent_at = await db.scalar(
            select(achievement_digests.c.sent_at).where(achievement_digests.c.id == digest_ids[0])
        )
    assert sent_again is False
    assert stored_sent_at is not None


def test_signed_unsubscribe_url_uses_the_configured_public_origin() -> None:
    user_id = UUID("b6b44404-ba79-4432-a1a1-860d70ad5724")

    url = signed_unsubscribe_url(
        public_app_url="https://api.example.test",
        api_prefix="/api/v1",
        user_id=user_id,
        secret="notification-secret",
    )

    assert url == (
        "https://api.example.test/api/v1/notifications/unsubscribe?"
        "token=b6b44404-ba79-4432-a1a1-860d70ad5724."
        "5befb82e2b9f054fead1f5093c414b3b00144402dd55e1af61a232dc007ced10"
    )


async def test_email_adapter_uses_injected_service_without_live_calls() -> None:
    messages: list[EmailMessage] = []

    class FakeEmailService:
        def send(self, message: EmailMessage) -> None:
            messages.append(message)

    sender = EmailServiceAchievementDigestSender(FakeEmailService())
    await sender.send(
        AchievementDigestDelivery(
            digest_id=uuid4(),
            workspace_id=uuid4(),
            recipient_email="digest@example.com",
            subject="Weekly wins",
            body="Summary",
            dashboard_url="https://app.example.test/dashboard",
            unsubscribe_url="https://app.example.test/unsubscribe/token",
        )
    )

    assert messages[0].to[0].email == "digest@example.com"
    assert "https://app.example.test/dashboard" in messages[0].text
    assert "https://app.example.test/unsubscribe/token" in messages[0].text


async def test_digest_workflow_delivers_only_eligible_rows_and_counts_idempotent_skips(
    digest_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = digest_client
    assert client.portal is not None
    _, _, _, digest_ids = client.portal.call(seed_digests, sessions)
    messages: list[EmailMessage] = []

    class FakeEmailService:
        def send(self, message: EmailMessage) -> None:
            messages.append(message)

    workflow = AchievementDigestWorkflow(
        sessions=sessions,
        email_service=FakeEmailService(),
        frontend_url="https://app.example.test",
        public_app_url="https://api.example.test",
        api_prefix="/api/v1",
        unsubscribe_secret="notification-secret",
    )

    first = await workflow.deliver("weekly")
    second = await workflow.deliver("weekly")

    assert first.sent == 2
    assert first.skipped == 0
    assert second.sent == second.skipped == 0
    assert len(messages) == 2
    assert messages[0].to[0].email == "digest@example.com"
    assert "https://app.example.test/dashboard" in messages[0].text
    assert "https://api.example.test/api/v1/notifications/unsubscribe?token=" in messages[0].text
    async with sessions() as db:
        sent_at = await db.scalar(
            select(achievement_digests.c.sent_at).where(achievement_digests.c.id == digest_ids[0])
        )
        other_sent_at = await db.scalar(
            select(achievement_digests.c.sent_at).where(achievement_digests.c.id == digest_ids[2])
        )
    assert sent_at is not None
    assert other_sent_at is not None
