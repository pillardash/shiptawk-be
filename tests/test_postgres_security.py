import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domains.auth.browser_service import provision_oauth_session
from app.domains.auth.models import OAuthIdentity
from app.domains.legacy.models import (
    github_raw_event_consumers,
    github_raw_events,
    normalized_events,
    product_repositories,
    repos,
)
from app.domains.products.models import Product
from app.domains.users.models import User
from app.domains.workspaces.models import Workspace
from app.services.github_webhook import GitHubWebhookEnvelope, ingest_github_webhook
from app.services.oauth import OAuthIdentityData

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")


async def test_postgres_rejects_cross_workspace_product_repository() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        user = User(email=f"tenant-{uuid4()}@example.com")
        workspace_a = Workspace(name="Tenant A")
        workspace_b = Workspace(name="Tenant B")
        db.add_all([user, workspace_a, workspace_b])
        await db.flush()
        product = Product(workspace_id=workspace_a.id, user_id=user.id, name="Product")
        db.add(product)
        await db.flush()
        repo_id = uuid4()
        await db.execute(
            insert(repos).values(
                id=repo_id,
                workspace_id=workspace_b.id,
                user_id=user.id,
                repo_name="repo",
                repo_full_name=f"test/{repo_id}",
                is_tracked=False,
            )
        )
        await db.flush()

        with pytest.raises(IntegrityError):
            await db.execute(
                insert(product_repositories).values(
                    id=uuid4(),
                    workspace_id=workspace_a.id,
                    product_id=product.id,
                    repo_id=repo_id,
                )
            )
            await db.flush()
        await db.rollback()
    await engine.dispose()


async def test_postgres_concurrent_first_oauth_login_creates_one_identity() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"concurrent-{uuid4()}"
    identity = OAuthIdentityData(
        subject=subject,
        email=f"{subject}@example.com",
        email_verified=True,
        username="concurrent",
    )

    async def login() -> None:
        async with sessions() as db:
            await provision_oauth_session(
                db,
                provider="github",
                identity_data=identity,
                user_agent="test",
                ip_address="127.0.0.1",
            )

    await asyncio.gather(login(), login())
    async with sessions() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(OAuthIdentity)
            .where(
                OAuthIdentity.provider == "github",
                OAuthIdentity.provider_subject == subject,
            )
        )
        assert count == 1
    await engine.dispose()


async def test_postgres_allows_one_delivery_to_be_consumed_by_two_workspaces() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    delivery_id = f"shared-{uuid4()}"

    async with sessions.begin() as db:
        user = User(email=f"shared-{uuid4()}@example.com")
        workspace_a = Workspace(name="Shared A")
        workspace_b = Workspace(name="Shared B")
        db.add_all([user, workspace_a, workspace_b])
        await db.flush()
        repo_a_id = uuid4()
        repo_b_id = uuid4()
        await db.execute(
            insert(repos),
            [
                {
                    "id": repo_a_id,
                    "workspace_id": workspace_a.id,
                    "user_id": user.id,
                    "repo_name": "shared",
                    "repo_full_name": "example/shared",
                    "is_tracked": True,
                },
                {
                    "id": repo_b_id,
                    "workspace_id": workspace_b.id,
                    "user_id": user.id,
                    "repo_name": "shared",
                    "repo_full_name": "example/shared",
                    "is_tracked": True,
                },
            ],
        )
        raw_event_id = uuid4()
        await db.execute(
            insert(github_raw_events).values(
                id=raw_event_id,
                github_delivery_id=delivery_id,
                event_name="push",
                repo_full_name="example/shared",
                payload_ciphertext="encrypted-test-fixture",
                payload_key_version="test-v1",
            )
        )
        await db.execute(
            insert(github_raw_event_consumers),
            [
                {
                    "workspace_id": workspace_a.id,
                    "raw_event_id": raw_event_id,
                    "repo_id": repo_a_id,
                    "user_id": user.id,
                },
                {
                    "workspace_id": workspace_b.id,
                    "raw_event_id": raw_event_id,
                    "repo_id": repo_b_id,
                    "user_id": user.id,
                },
            ],
        )
        for workspace, repo_id, suffix in (
            (workspace_a, repo_a_id, "a"),
            (workspace_b, repo_b_id, "b"),
        ):
            await db.execute(
                insert(normalized_events).values(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    raw_event_id=raw_event_id,
                    repo_id=repo_id,
                    event_type="push",
                    repo_full_name="example/shared",
                    title="Shared delivery",
                    description="Sanitized normalized event",
                    url="https://example.test/event",
                    occurred_at=func.now(),
                    dedupe_key=f"{delivery_id}-{suffix}",
                )
            )

    async with sessions() as db:
        consumer_count = await db.scalar(
            select(func.count())
            .select_from(github_raw_event_consumers)
            .where(github_raw_event_consumers.c.raw_event_id == raw_event_id)
        )
        normalized_count = await db.scalar(
            select(func.count())
            .select_from(normalized_events)
            .where(normalized_events.c.raw_event_id == raw_event_id)
        )
        assert consumer_count == 2
        assert normalized_count == 2
    await engine.dispose()


async def test_postgres_rejects_normalized_event_without_workspace_consumer() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions.begin() as db:
        user = User(email=f"unconsumed-{uuid4()}@example.com")
        workspace = Workspace(name="Unconsumed")
        db.add_all([user, workspace])
        await db.flush()
        repo_id = uuid4()
        raw_event_id = uuid4()
        await db.execute(
            insert(repos).values(
                id=repo_id,
                workspace_id=workspace.id,
                user_id=user.id,
                repo_name="unconsumed",
                repo_full_name=f"example/{repo_id}",
                is_tracked=True,
            )
        )
        await db.execute(
            insert(github_raw_events).values(
                id=raw_event_id,
                github_delivery_id=f"unconsumed-{uuid4()}",
                event_name="push",
                repo_full_name="example/unconsumed",
                payload_ciphertext="encrypted-test-fixture",
                payload_key_version="test-v1",
            )
        )

    async with sessions() as db:
        with pytest.raises(IntegrityError):
            await db.execute(
                insert(normalized_events).values(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    raw_event_id=raw_event_id,
                    repo_id=repo_id,
                    event_type="push",
                    repo_full_name="example/unconsumed",
                    title="Unconsumed",
                    description="Sanitized normalized event",
                    url="https://example.test/event",
                    occurred_at=func.now(),
                    dedupe_key=f"unconsumed-{uuid4()}",
                )
            )
            await db.flush()
    await engine.dispose()


async def test_postgres_concurrent_duplicate_delivery_creates_one_raw_event() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    delivery_id = f"race-{uuid4()}"

    async def insert_delivery() -> bool:
        try:
            async with sessions.begin() as db:
                await db.execute(
                    insert(github_raw_events).values(
                        github_delivery_id=delivery_id,
                        event_name="push",
                        repo_full_name="example/race",
                        payload_ciphertext="encrypted-test-fixture",
                        payload_key_version="test-v1",
                    )
                )
            return True
        except IntegrityError:
            return False

    outcomes = await asyncio.gather(insert_delivery(), insert_delivery())
    assert sorted(outcomes) == [False, True]

    async with sessions() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(github_raw_events)
            .where(github_raw_events.c.github_delivery_id == delivery_id)
        )
    assert count == 1
    await engine.dispose()


async def test_postgres_concurrent_webhook_ingestion_creates_one_consumer_outcome() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    delivery_id = f"webhook-race-{uuid4()}"
    repo_full_name = f"example/{uuid4()}"
    encryption_key = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="

    async with sessions.begin() as db:
        user = User(email=f"webhook-race-{uuid4()}@example.com")
        workspace = Workspace(name="Webhook race")
        db.add_all([user, workspace])
        await db.flush()
        await db.execute(
            insert(repos).values(
                id=uuid4(),
                workspace_id=workspace.id,
                user_id=user.id,
                repo_name="race",
                repo_full_name=repo_full_name,
                is_tracked=True,
            )
        )

    envelope = GitHubWebhookEnvelope(
        delivery_id=delivery_id,
        event_name="push",
        repo_full_name=repo_full_name,
    )

    async def ingest() -> tuple[bool, int]:
        async with sessions() as db:
            result = await ingest_github_webhook(
                db,
                envelope=envelope,
                raw_body=b'{"repository":{"full_name":"example/race"}}',
                encryption_key=encryption_key,
            )
            return result.duplicate, result.consumer_count

    outcomes = await asyncio.gather(ingest(), ingest())

    assert sorted(outcomes) == [(False, 1), (True, 1)]
    async with sessions() as db:
        raw_count = await db.scalar(
            select(func.count())
            .select_from(github_raw_events)
            .where(github_raw_events.c.github_delivery_id == delivery_id)
        )
        consumer_count = await db.scalar(
            select(func.count())
            .select_from(github_raw_event_consumers)
            .join(github_raw_events)
            .where(github_raw_events.c.github_delivery_id == delivery_id)
        )
        assert raw_count == 1
        assert consumer_count == 1
    await engine.dispose()
