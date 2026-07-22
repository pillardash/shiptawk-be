from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.domains.drafts.publisher import (
    FakePublisher,
    HttpXPublisher,
    PublisherTransientError,
    PublishRequest,
    StaticCredentialDecryptor,
)
from app.domains.drafts.router import router as drafts_router
from app.domains.drafts.service import publish_draft
from app.domains.integrations.models import IntegrationConnection
from app.domains.integrations.router import router as integrations_router
from app.domains.legacy.models import draft_feedback_events, draft_status_events, drafts, repos
from app.domains.users.models import User
from app.domains.workspaces.models import Workspace, WorkspaceMembership, WorkspaceRole
from app.main import create_app
from app.shared.exceptions import ConflictError, ServiceUnavailableError


@pytest.fixture
def publishing_client() -> Generator[
    tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher], None, None
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    publisher = FakePublisher()

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
    app.include_router(drafts_router, prefix="/api/v1")
    app.include_router(integrations_router, prefix="/api/v1")
    app.state.publisher = publisher
    app.state.integration_credential_decryptor = StaticCredentialDecryptor(
        {"encrypted-x-credentials": {"accessToken": "test-token"}}
    )
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, sessions, publisher
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


async def seed_publish_data(
    sessions: async_sessionmaker[AsyncSession], status: str = "approved"
) -> tuple[User, Workspace, Workspace, UUID]:
    async with sessions() as db:
        user = User(email=f"publisher-{uuid4()}@example.com")
        workspace = Workspace(name="Publishing workspace")
        other_workspace = Workspace(name="Other workspace")
        db.add_all([user, workspace, other_workspace])
        await db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role=WorkspaceRole.owner,
                    is_active=True,
                ),
                WorkspaceMembership(
                    workspace_id=other_workspace.id,
                    user_id=user.id,
                    role=WorkspaceRole.owner,
                    is_active=True,
                ),
                IntegrationConnection(
                    workspace_id=workspace.id,
                    provider="x",
                    external_account_id="x-account-1",
                    external_username="shiptawk_test",
                    credentials_ciphertext="encrypted-x-credentials",
                    credential_key_version="v1",
                    scopes=["tweet.read", "tweet.write", "users.read"],
                    status="active",
                    idempotency_key=f"x-connection:{workspace.id}",
                ),
            ]
        )
        repo_id = uuid4()
        draft_id = uuid4()
        await db.execute(
            insert(repos).values(
                id=repo_id,
                workspace_id=workspace.id,
                user_id=user.id,
                repo_name="backend",
                repo_full_name="example/backend",
            )
        )
        await db.execute(
            insert(drafts).values(
                id=draft_id,
                workspace_id=workspace.id,
                user_id=user.id,
                repo_id=repo_id,
                content="An explicitly approved public update",
                source_event_type="release",
                source_event_payload={},
                status=status,
                approved_at=datetime.now(UTC) if status == "approved" else None,
            )
        )
        await db.commit()
        return user, workspace, other_workspace, draft_id


def auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def publish_state(
    sessions: async_sessionmaker[AsyncSession], draft_id: UUID
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    async with sessions() as db:
        draft = (await db.execute(select(drafts).where(drafts.c.id == draft_id))).mappings().one()
        statuses = (
            await db.execute(
                select(draft_status_events).where(draft_status_events.c.draft_id == draft_id)
            )
        ).mappings()
        feedback = (
            await db.execute(
                select(draft_feedback_events).where(draft_feedback_events.c.draft_id == draft_id)
            )
        ).mappings()
        return dict(draft), [dict(row) for row in statuses], [dict(row) for row in feedback]


def test_publish_route_posts_once_and_returns_stable_receipt(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions)
    url = f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/publish"

    first = client.post(url, headers=auth_headers(user))
    second = client.post(url, headers=auth_headers(user))

    assert first.status_code == second.status_code == 200
    expected_receipt = {
        "draftId": str(draft_id),
        "provider": "x",
        "providerPostId": "fake-post-1",
        "url": "https://x.example/fake-post-1",
        "idempotencyKey": f"x:draft:{draft_id}",
    }
    assert first.json() == expected_receipt | {"alreadyPosted": False}
    assert second.json() == expected_receipt | {"alreadyPosted": True}
    assert first.json()["alreadyPosted"] is False
    assert second.json()["alreadyPosted"] is True
    assert publisher.requests == [
        PublishRequest(
            content="An explicitly approved public update",
            credentials={"accessToken": "test-token"},
            idempotency_key=f"x:draft:{draft_id}",
        )
    ]
    draft, statuses, feedback = client.portal.call(publish_state, sessions, draft_id)
    assert draft["status"] == "posted"
    assert draft["tweet_id"] == "fake-post-1"
    assert draft["posting_started_at"] is None
    assert [event["status"] for event in statuses] == ["posted"]
    assert [event["event_type"] for event in feedback] == ["posted"]
    assert feedback[0]["provider_post_id"] == "fake-post-1"
    assert feedback[0]["metadata"] == {
        "provider": "x",
        "url": "https://x.example/fake-post-1",
        "idempotencyKey": f"x:draft:{draft_id}",
    }


def test_startup_initializes_the_http_x_publisher_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.main.get_settings",
        lambda: Settings(
            x_publishing_enabled=True,
            integration_credentials_encryption_key="BfXDdHwnSM6lVTfzq3hFhAOWc2KX6M3GnE_2MdYTpmo=",
        ),
    )

    app = create_app()

    assert app.state.x_publishing_enabled is True
    assert isinstance(app.state.publisher, HttpXPublisher)


def test_publish_never_calls_provider_without_explicit_approval(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions, "pending")

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/publish",
        headers=auth_headers(user),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "draft_publish_requires_approval"
    assert publisher.requests == []


def test_transient_provider_failure_is_explicit_and_does_not_mark_posted(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions)
    publisher.error = PublisherTransientError("X is temporarily unavailable")

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/publish",
        headers=auth_headers(user),
    )

    assert response.status_code == 503
    assert response.json()["code"] == "publisher_transient_failure"
    draft, statuses, feedback = client.portal.call(publish_state, sessions, draft_id)
    assert draft["status"] == "approved"
    assert draft["tweet_id"] is None
    assert statuses == feedback == []


def test_publish_requires_authorized_role_and_matching_workspace(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, other_workspace, draft_id = client.portal.call(seed_publish_data, sessions)

    async def make_viewer() -> None:
        async with sessions() as db:
            membership = await db.get(
                WorkspaceMembership,
                {"workspace_id": workspace.id, "user_id": user.id},
            )
            assert membership is not None
            membership.role = WorkspaceRole.viewer
            await db.commit()

    client.portal.call(make_viewer)
    forbidden = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/publish",
        headers=auth_headers(user),
    )
    cross_workspace = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/drafts/{draft_id}/publish",
        headers=auth_headers(user),
    )

    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "draft_publish_forbidden"
    assert cross_workspace.status_code == 404
    assert cross_workspace.json()["code"] == "draft_not_found"
    assert publisher.requests == []


def test_publish_service_exposes_controlled_domain_errors(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions, "pending")

    async def call_service() -> None:
        async with sessions() as db:
            with pytest.raises(ConflictError):
                await publish_draft(
                    db,
                    workspace.id,
                    draft_id,
                    user.id,
                    publisher,
                    StaticCredentialDecryptor({}),
                )

            await db.execute(
                drafts.update()
                .where(drafts.c.id == draft_id)
                .values(status="approved", approved_at=datetime.now(UTC))
            )
            await db.commit()
            publisher.error = PublisherTransientError("retry")
            with pytest.raises(ServiceUnavailableError):
                await publish_draft(
                    db,
                    workspace.id,
                    draft_id,
                    user.id,
                    publisher,
                    StaticCredentialDecryptor(
                        {"encrypted-x-credentials": {"accessToken": "test-token"}}
                    ),
                )

    client.portal.call(call_service)


def test_x_connection_status_and_disconnect_are_tenant_scoped(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, _ = publishing_client
    assert client.portal is not None
    user, workspace, other_workspace, _ = client.portal.call(seed_publish_data, sessions)
    headers = auth_headers(user)

    connected = client.get(f"/api/v1/workspaces/{workspace.id}/x/connection", headers=headers)
    absent = client.get(f"/api/v1/workspaces/{other_workspace.id}/x/connection", headers=headers)
    disconnected = client.delete(f"/api/v1/workspaces/{workspace.id}/x/connection", headers=headers)

    assert connected.status_code == absent.status_code == disconnected.status_code == 200
    assert connected.json() == {
        "connected": True,
        "status": "active",
        "accountId": "x-account-1",
        "username": "shiptawk_test",
        "scopes": ["tweet.read", "tweet.write", "users.read"],
        "tokenExpiresAt": None,
    }
    assert absent.json()["connected"] is False
    assert absent.json()["accountId"] is None
    assert disconnected.json()["connected"] is False
    assert "credentialsCiphertext" not in str(connected.json())
