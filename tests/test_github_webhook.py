import hashlib
import hmac
import json
from collections.abc import AsyncGenerator, Generator
from typing import cast
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.modules.identity.models.users import User
from app.modules.integrations.models import github_raw_event_consumers, github_raw_events
from app.modules.repos.models import repos
from app.modules.workspaces.models import Workspace
from app.services.github_webhook import decrypt_github_webhook_payload
from app.workflows.publisher import MetadataEvent

WEBHOOK_SECRET = "test-webhook-secret"
ENCRYPTION_KEY = Fernet.generate_key().decode()


class FakeMetadataPublisher:
    def __init__(self) -> None:
        self.events: list[MetadataEvent] = []

    async def publish(self, event: MetadataEvent) -> None:
        self.events.append(event)


def signed_headers(
    body: bytes, *, delivery: str = "delivery-1", event: str = "push"
) -> dict[str, str]:
    signature = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={signature}",
        "X-GitHub-Delivery": delivery,
        "X-GitHub-Event": event,
        "Content-Type": "application/json",
    }


@pytest.fixture
def webhook_client(monkeypatch: MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("GITHUB_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("GITHUB_WEBHOOK_PAYLOAD_ENCRYPTION_KEY", ENCRYPTION_KEY)
    monkeypatch.setenv("GITHUB_WEBHOOK_MAX_BODY_BYTES", "512")
    monkeypatch.setenv("INNGEST_ENABLED", "true")
    monkeypatch.setenv("INNGEST_EVENT_KEY", "test-event-key")
    monkeypatch.setenv("GENERATION_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    app = create_app()
    app.state.workflow_event_publisher = FakeMetadataPublisher()
    app.state.testing_session = sessions
    app.dependency_overrides[get_db] = override_get_db

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


def payload(repo: str = "acme/api") -> bytes:
    return json.dumps({"repository": {"full_name": repo}}, separators=(",", ":")).encode()


def post(client: TestClient, body: bytes, **header_overrides: str) -> httpx.Response:
    headers = signed_headers(body)
    headers.update(header_overrides)
    return client.post("/v1/webhooks/github", content=body, headers=headers)


def seed_tracked_repositories(client: TestClient, count: int, repo: str = "acme/api") -> None:
    assert client.portal is not None
    sessions = cast(FastAPI, client.app).state.testing_session

    async def seed() -> None:
        async with sessions.begin() as db:
            for number in range(count):
                user = User(email=f"owner-{number}-{uuid4()}@example.com")
                workspace = Workspace(name=f"Workspace {number}")
                db.add_all([user, workspace])
                await db.flush()
                await db.execute(
                    insert(repos).values(
                        id=uuid4(),
                        workspace_id=workspace.id,
                        user_id=user.id,
                        repo_name=repo.split("/", 1)[1],
                        repo_full_name=repo,
                        is_tracked=True,
                    )
                )

    client.portal.call(seed)


def persisted_counts(client: TestClient) -> tuple[int, int]:
    assert client.portal is not None
    sessions = cast(FastAPI, client.app).state.testing_session

    async def counts() -> tuple[int, int]:
        async with sessions() as db:
            raw = await db.scalar(select(func.count()).select_from(github_raw_events))
            consumers = await db.scalar(
                select(func.count()).select_from(github_raw_event_consumers)
            )
            return int(raw or 0), int(consumers or 0)

    return client.portal.call(counts)


@pytest.mark.parametrize("signature", [None, "sha256=bad", "sha1=bad"])
def test_webhook_rejects_missing_or_bad_signature(
    webhook_client: TestClient, signature: str | None
) -> None:
    body = payload()
    headers = signed_headers(body)
    if signature is None:
        headers.pop("X-Hub-Signature-256")
    else:
        headers["X-Hub-Signature-256"] = signature

    response = webhook_client.post("/v1/webhooks/github", content=body, headers=headers)

    assert response.status_code == 401
    assert persisted_counts(webhook_client) == (0, 0)


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        (b"not-json", {}),
        (b"[]", {}),
        (json.dumps({"repository": {}}).encode(), {}),
        (payload(), {"X-GitHub-Delivery": ""}),
        (payload(), {"X-GitHub-Event": ""}),
    ],
)
def test_webhook_rejects_malformed_envelope(
    webhook_client: TestClient, body: bytes, headers: dict[str, str]
) -> None:
    response = post(webhook_client, body, **headers)

    assert response.status_code == 400
    assert persisted_counts(webhook_client) == (0, 0)


def test_webhook_rejects_oversize_body(webhook_client: TestClient) -> None:
    body = json.dumps({"repository": {"full_name": "acme/api"}, "padding": "x" * 600}).encode()

    response = post(webhook_client, body)

    assert response.status_code == 413
    assert persisted_counts(webhook_client) == (0, 0)


def test_unsupported_event_is_accepted_and_ignored(webhook_client: TestClient) -> None:
    body = payload()

    response = post(webhook_client, body, **{"X-GitHub-Event": "installation"})

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "ignored": True,
        "duplicate": False,
        "consumerCount": 0,
    }
    assert persisted_counts(webhook_client) == (0, 0)


def test_untracked_repository_persists_only_encrypted_raw_event(
    webhook_client: TestClient,
) -> None:
    body = payload()

    response = post(webhook_client, body)

    assert response.status_code == 202
    assert response.json()["consumerCount"] == 0
    assert persisted_counts(webhook_client) == (1, 0)
    assert webhook_client.portal is not None
    sessions = cast(FastAPI, webhook_client.app).state.testing_session

    async def ciphertext() -> tuple[str, str]:
        async with sessions() as db:
            row = (await db.execute(select(github_raw_events))).one()
            return row.payload_ciphertext, row.payload_key_version

    encrypted, key_version = webhook_client.portal.call(ciphertext)
    assert body.decode() not in encrypted
    assert decrypt_github_webhook_payload(encrypted, ENCRYPTION_KEY) == body
    assert key_version == "fernet-v1"


def test_one_delivery_creates_consumers_for_two_tracking_workspaces(
    webhook_client: TestClient,
) -> None:
    seed_tracked_repositories(webhook_client, 2)
    body = payload()

    response = post(webhook_client, body)

    assert response.json()["consumerCount"] == 2
    assert persisted_counts(webhook_client) == (1, 2)
    assert webhook_client.portal is not None
    sessions = cast(FastAPI, webhook_client.app).state.testing_session

    async def states() -> list[tuple[str, int]]:
        async with sessions() as db:
            rows = await db.execute(
                select(
                    github_raw_event_consumers.c.processing_state,
                    github_raw_event_consumers.c.publish_attempts,
                )
            )
            return [(row.processing_state, row.publish_attempts) for row in rows]

    assert webhook_client.portal.call(states) == [("enqueued", 1), ("enqueued", 1)]
    app = cast(FastAPI, webhook_client.app)
    publisher = cast(FakeMetadataPublisher, app.state.workflow_event_publisher)
    assert len(publisher.events) == 2


def test_duplicate_delivery_has_same_outcome_without_duplicate_consumers(
    webhook_client: TestClient,
) -> None:
    seed_tracked_repositories(webhook_client, 2)
    body = payload()

    first = post(webhook_client, body)
    second_headers = signed_headers(body, delivery="delivery-1")
    second = webhook_client.post("/v1/webhooks/github", content=body, headers=second_headers)

    assert first.status_code == second.status_code == 202
    assert first.json()["consumerCount"] == second.json()["consumerCount"] == 2
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert persisted_counts(webhook_client) == (1, 2)


def test_openapi_does_not_expose_restricted_webhook_payload_fields(
    webhook_client: TestClient,
) -> None:
    schema = webhook_client.get("/openapi.json").json()
    public_schemas = json.dumps(schema.get("components", {}).get("schemas", {}))

    assert "payloadCiphertext" not in public_schemas
    assert "payloadKeyVersion" not in public_schemas
    assert "/v1/webhooks/github" in schema["paths"]
