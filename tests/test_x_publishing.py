from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from functools import partial
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.exception_handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.modules.drafts.api.router import router as drafts_router
from app.modules.drafts.models import draft_feedback_events, draft_status_events, drafts
from app.modules.drafts.providers.fakes import FakePublisher, StaticCredentialDecryptor
from app.modules.drafts.providers.protocols import (
    PublisherPermanentError,
    PublisherTransientError,
    PublishRequest,
)
from app.modules.drafts.providers.x import HttpXPublisher
from app.modules.drafts.services.review import publish_draft
from app.modules.identity.models.users import User
from app.modules.integrations.api.router import router as integrations_router
from app.modules.integrations.models import IntegrationConnection
from app.modules.operator.models import PreparedAsset
from app.modules.repos.models import repos
from app.modules.workspaces.models import Workspace, WorkspaceMembership, WorkspaceRole
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

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(drafts_router, prefix="/v1")
    app.include_router(integrations_router, prefix="/v1")
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


async def attach_exact_x_revision(
    sessions: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
    draft_id: UUID,
    *,
    revision: int = 1,
) -> UUID:
    asset_id = uuid4()
    async with sessions() as db:
        db.add(
            PreparedAsset(
                id=asset_id,
                workspace_id=workspace_id,
                product_id=uuid4(),
                recommendation_id=uuid4(),
                llm_execution_id=uuid4(),
                revision=revision,
                kind="x_draft",
                structured_content={
                    "kind": "x_draft",
                    "content": {"post": "An explicitly approved public update"},
                    "claim_evidence": [],
                },
            )
        )
        await db.execute(
            drafts.update()
            .where(drafts.c.id == draft_id, drafts.c.workspace_id == workspace_id)
            .values(prepared_asset_id=asset_id, prepared_asset_revision=revision)
        )
        await db.commit()
    return asset_id


async def update_x_connection(
    sessions: async_sessionmaker[AsyncSession], workspace_id: UUID, **values: object
) -> None:
    async with sessions() as db:
        await db.execute(
            update(IntegrationConnection)
            .where(IntegrationConnection.workspace_id == workspace_id)
            .values(**values)
        )
        await db.commit()


def test_publish_route_posts_once_and_returns_stable_receipt(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions)
    url = f"/v1/workspaces/{workspace.id}/drafts/{draft_id}/publish"

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


def test_approve_and_publish_command_replays_without_duplicate_side_effect(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions, "pending")
    asset_id = client.portal.call(attach_exact_x_revision, sessions, workspace.id, draft_id)
    url = f"/v1/workspaces/{workspace.id}/drafts/{draft_id}/approve-and-publish"
    headers = auth_headers(user) | {"Idempotency-Key": "publish-command-1"}
    payload = {"assetId": str(asset_id), "revision": 1, "explicitApproval": True}

    first = client.post(url, headers=headers, json=payload)
    replay = client.post(url, headers=headers, json=payload)

    assert first.status_code == replay.status_code == 200
    assert first.json()["outcome"] == first.json()["originalOutcome"] == "success"
    assert replay.json()["outcome"] == "replay"
    assert replay.json()["originalOutcome"] == "success"
    assert replay.json()["providerPostId"] == first.json()["providerPostId"]
    assert len(publisher.requests) == 1


def test_approve_and_publish_rejects_idempotency_payload_conflict_and_wrong_revision(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions)
    asset_id = client.portal.call(attach_exact_x_revision, sessions, workspace.id, draft_id)
    url = f"/v1/workspaces/{workspace.id}/drafts/{draft_id}/approve-and-publish"
    headers = auth_headers(user) | {"Idempotency-Key": "conflicting-command"}
    valid = {"assetId": str(asset_id), "revision": 1, "explicitApproval": True}
    assert client.post(url, headers=headers, json=valid).status_code == 200

    conflict = client.post(url, headers=headers, json=valid | {"revision": 2})
    wrong_revision = client.post(
        url,
        headers=auth_headers(user) | {"Idempotency-Key": "wrong-revision"},
        json=valid | {"revision": 2},
    )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "publication_idempotency_conflict"
    assert wrong_revision.status_code == 409
    assert wrong_revision.json()["code"] == "draft_revision_conflict"
    assert len(publisher.requests) == 1


def test_approve_and_publish_returns_durable_failed_result_when_x_is_disconnected(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions, "pending")
    asset_id = client.portal.call(attach_exact_x_revision, sessions, workspace.id, draft_id)
    client.delete(f"/v1/workspaces/{workspace.id}/x/connection", headers=auth_headers(user))
    url = f"/v1/workspaces/{workspace.id}/drafts/{draft_id}/approve-and-publish"
    headers = auth_headers(user) | {"Idempotency-Key": "disconnected-command"}
    payload = {"assetId": str(asset_id), "revision": 1, "explicitApproval": True}

    failed = client.post(url, headers=headers, json=payload)
    replay = client.post(url, headers=headers, json=payload)

    assert failed.json()["outcome"] == "failed"
    assert failed.json()["errorCode"] == "x_connection_required"
    assert replay.json()["outcome"] == "replay"
    assert replay.json()["originalOutcome"] == "failed"
    assert publisher.requests == []


def test_approve_and_publish_unknown_outcome_is_replayed_without_retrying_provider(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions)
    asset_id = client.portal.call(attach_exact_x_revision, sessions, workspace.id, draft_id)
    publisher.error = PublisherTransientError("connection dropped after send")
    url = f"/v1/workspaces/{workspace.id}/drafts/{draft_id}/approve-and-publish"
    headers = auth_headers(user) | {"Idempotency-Key": "unknown-command"}
    payload = {"assetId": str(asset_id), "revision": 1, "explicitApproval": True}

    unknown = client.post(url, headers=headers, json=payload)
    publisher.error = None
    replay = client.post(url, headers=headers, json=payload)

    assert unknown.json()["outcome"] == "unknown"
    assert unknown.json()["errorCode"] == "publisher_unknown_outcome"
    assert replay.json()["outcome"] == "replay"
    assert replay.json()["originalOutcome"] == "unknown"
    assert publisher.requests == []


def test_approve_and_publish_permanent_failure_is_durable_and_replayed(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions)
    asset_id = client.portal.call(attach_exact_x_revision, sessions, workspace.id, draft_id)
    publisher.error = PublisherPermanentError("provider rejected content")
    url = f"/v1/workspaces/{workspace.id}/drafts/{draft_id}/approve-and-publish"
    headers = auth_headers(user) | {"Idempotency-Key": "rejected-command"}
    payload = {"assetId": str(asset_id), "revision": 1, "explicitApproval": True}

    failed = client.post(url, headers=headers, json=payload)
    publisher.error = None
    replay = client.post(url, headers=headers, json=payload)

    assert failed.status_code == replay.status_code == 200
    assert failed.json()["outcome"] == failed.json()["originalOutcome"] == "failed"
    assert failed.json()["errorCode"] == "publisher_rejected"
    assert replay.json()["outcome"] == "replay"
    assert replay.json()["originalOutcome"] == "failed"
    assert publisher.requests == []


@pytest.mark.parametrize(
    ("connection_values", "error_code"),
    [
        ({"scopes": ["tweet.read"]}, "x_write_scope_required"),
        ({"credentials_ciphertext": None}, "x_credentials_unavailable"),
        ({"credential_key_version": None}, "x_credentials_unavailable"),
        ({"credentials_ciphertext": "unknown-ciphertext"}, "x_credentials_unavailable"),
    ],
)
def test_approve_and_publish_fails_before_provider_for_unusable_connection(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
    connection_values: dict[str, object],
    error_code: str,
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions, "pending")
    asset_id = client.portal.call(attach_exact_x_revision, sessions, workspace.id, draft_id)
    client.portal.call(partial(update_x_connection, sessions, workspace.id, **connection_values))

    response = client.post(
        f"/v1/workspaces/{workspace.id}/drafts/{draft_id}/approve-and-publish",
        headers=auth_headers(user) | {"Idempotency-Key": f"connection-{error_code}-{uuid4()}"},
        json={"assetId": str(asset_id), "revision": 1, "explicitApproval": True},
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "failed"
    assert response.json()["errorCode"] == error_code
    assert publisher.requests == []


def test_approve_and_publish_hides_cross_workspace_revision(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, other_workspace, draft_id = client.portal.call(seed_publish_data, sessions)
    asset_id = client.portal.call(attach_exact_x_revision, sessions, workspace.id, draft_id)

    response = client.post(
        f"/v1/workspaces/{other_workspace.id}/drafts/{draft_id}/approve-and-publish",
        headers=auth_headers(user) | {"Idempotency-Key": "cross-workspace-command"},
        json={"assetId": str(asset_id), "revision": 1, "explicitApproval": True},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "draft_not_found"
    assert publisher.requests == []


def test_http_x_publisher_can_be_initialized_when_enabled() -> None:
    settings = Settings(
        x_publishing_enabled=True,
        integration_credentials_encryption_key="BfXDdHwnSM6lVTfzq3hFhAOWc2KX6M3GnE_2MdYTpmo=",
    )
    publisher = (
        HttpXPublisher(timeout_seconds=settings.oauth_http_timeout_seconds)
        if settings.x_publishing_enabled
        else None
    )

    assert settings.x_publishing_enabled is True
    assert isinstance(publisher, HttpXPublisher)


def test_publish_never_calls_provider_without_explicit_approval(
    publishing_client: tuple[TestClient, async_sessionmaker[AsyncSession], FakePublisher],
) -> None:
    client, sessions, publisher = publishing_client
    assert client.portal is not None
    user, workspace, _, draft_id = client.portal.call(seed_publish_data, sessions, "pending")

    response = client.post(
        f"/v1/workspaces/{workspace.id}/drafts/{draft_id}/publish",
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
        f"/v1/workspaces/{workspace.id}/drafts/{draft_id}/publish",
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
        f"/v1/workspaces/{workspace.id}/drafts/{draft_id}/publish",
        headers=auth_headers(user),
    )
    cross_workspace = client.post(
        f"/v1/workspaces/{other_workspace.id}/drafts/{draft_id}/publish",
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

    connected = client.get(f"/v1/workspaces/{workspace.id}/x/connection", headers=headers)
    absent = client.get(f"/v1/workspaces/{other_workspace.id}/x/connection", headers=headers)
    disconnected = client.delete(f"/v1/workspaces/{workspace.id}/x/connection", headers=headers)

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
