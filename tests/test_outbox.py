from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.outbox import (
    OutboxEvent,
    OutboxMetadata,
    OutboxStatus,
    claim_outbox_events,
    enqueue_outbox_event,
    mark_outbox_event_published,
    release_outbox_event_for_retry,
)


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all, tables=[cast(Table, OutboxEvent.__table__)]
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield session
    await engine.dispose()


async def test_enqueue_flushes_without_committing_and_duplicate_replays(
    db: AsyncSession,
) -> None:
    event_id = uuid4()
    first = await enqueue_outbox_event(
        db,
        event_name="integration.received",
        schema_version=1,
        idempotency_key="delivery-123",
        metadata=OutboxMetadata.model_validate({"deliveryId": "123"}),
        event_id=event_id,
    )
    replay = await enqueue_outbox_event(
        db,
        event_name="integration.received",
        schema_version=1,
        idempotency_key="delivery-123",
        metadata=OutboxMetadata.model_validate({"deliveryId": "ignored-on-replay"}),
    )

    assert first.inserted is True
    assert replay.inserted is False
    assert replay.value.id == event_id
    assert replay.value.metadata_payload == {"deliveryId": "123"}
    assert not db.in_nested_transaction()


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": "private instructions"},
        {"accessToken": "secret"},
        {"body": "unrestricted payload"},
        {"nested": {"content": "source"}},
        {"tooLong": "x" * 2049},
    ],
)
def test_outbox_metadata_rejects_sensitive_or_content_payloads(payload: object) -> None:
    with pytest.raises(ValidationError):
        OutboxMetadata.model_validate(payload)


def test_outbox_metadata_accepts_identifiers_and_delivery_attributes() -> None:
    metadata = OutboxMetadata.model_validate(
        {
            "deliveryId": "123",
            "repositoryId": 42,
            "sourceId": "source-123",
            "retry": False,
            "labels": ["push", "v1"],
        }
    )

    assert metadata.root["repositoryId"] == 42
    assert metadata.root["sourceId"] == "source-123"


async def test_claim_leases_only_available_pending_events_in_tenant_scope(
    db: AsyncSession,
) -> None:
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    now = datetime.now(UTC)
    for key, tenant_id, available_at in (
        ("ready", workspace_id, now - timedelta(seconds=1)),
        ("future", workspace_id, now + timedelta(hours=1)),
        ("other", other_workspace_id, now - timedelta(seconds=1)),
    ):
        await enqueue_outbox_event(
            db,
            event_name="test.event",
            schema_version=1,
            idempotency_key=key,
            metadata=OutboxMetadata.model_validate({"key": key}),
            workspace_id=tenant_id,
            available_at=available_at,
        )

    claimed = await claim_outbox_events(
        db,
        limit=10,
        lease_duration=timedelta(minutes=2),
        now=now,
        workspace_id=workspace_id,
    )

    assert [event.idempotency_key for event in claimed] == ["ready"]
    assert claimed[0].status == OutboxStatus.PUBLISHING
    assert claimed[0].lease_expires_at == now + timedelta(minutes=2)
    assert claimed[0].attempts == 1


async def test_expired_lease_can_be_claimed_again(db: AsyncSession) -> None:
    now = datetime.now(UTC)
    result = await enqueue_outbox_event(
        db,
        event_name="test.event",
        schema_version=1,
        idempotency_key="expired",
        metadata=OutboxMetadata.model_validate({"key": "expired"}),
        available_at=now,
    )
    event = result.value
    event.status = OutboxStatus.PUBLISHING
    event.lease_expires_at = now - timedelta(seconds=1)
    await db.flush()

    claimed = await claim_outbox_events(db, limit=1, lease_duration=timedelta(minutes=1), now=now)

    assert claimed[0].id == event.id
    assert claimed[0].attempts == 1


async def test_mark_published_clears_lease_and_sets_timestamp(db: AsyncSession) -> None:
    now = datetime.now(UTC)
    await enqueue_outbox_event(
        db,
        event_name="test.event",
        schema_version=1,
        idempotency_key="publish",
        metadata=OutboxMetadata.model_validate({"key": "publish"}),
        available_at=now,
    )
    claimed = await claim_outbox_events(db, limit=1, lease_duration=timedelta(minutes=1), now=now)

    published = await mark_outbox_event_published(db, claimed[0].id, published_at=now)

    assert published.status == OutboxStatus.PUBLISHED
    assert published.published_at == now
    assert published.lease_expires_at is None
    assert published.last_error_category is None


async def test_retry_sanitizes_category_and_reschedules(db: AsyncSession) -> None:
    now = datetime.now(UTC)
    await enqueue_outbox_event(
        db,
        event_name="test.event",
        schema_version=1,
        idempotency_key="retry",
        metadata=OutboxMetadata.model_validate({"key": "retry"}),
        available_at=now,
    )
    claimed = await claim_outbox_events(db, limit=1, lease_duration=timedelta(minutes=1), now=now)

    retried = await release_outbox_event_for_retry(
        db,
        claimed[0].id,
        error_category="HTTP 429: token=must-not-be-stored",
        available_at=now + timedelta(minutes=5),
    )

    assert retried.status == OutboxStatus.PENDING
    assert retried.available_at == now + timedelta(minutes=5)
    assert retried.lease_expires_at is None
    assert retried.last_error_category == "http_429"
    assert len(retried.last_error_category) <= 64


async def test_failed_event_is_not_claimed(db: AsyncSession) -> None:
    result = await enqueue_outbox_event(
        db,
        event_name="test.event",
        schema_version=1,
        idempotency_key="failed",
        metadata=OutboxMetadata.model_validate({"key": "failed"}),
    )
    result.value.status = OutboxStatus.FAILED
    await db.flush()

    assert await claim_outbox_events(db, limit=1, lease_duration=timedelta(minutes=1)) == []
    stored = await db.scalar(select(OutboxEvent).where(OutboxEvent.id == result.value.id))
    assert stored is result.value


async def test_retry_marks_event_failed_at_attempt_limit(db: AsyncSession) -> None:
    now = datetime.now(UTC)
    await enqueue_outbox_event(
        db,
        event_name="test.event",
        schema_version=1,
        idempotency_key="attempt-limit",
        metadata=OutboxMetadata.model_validate({"key": "attempt-limit"}),
        available_at=now,
    )
    claimed = await claim_outbox_events(db, limit=1, lease_duration=timedelta(minutes=1), now=now)

    failed = await release_outbox_event_for_retry(
        db,
        claimed[0].id,
        error_category="provider_timeout",
        available_at=now + timedelta(minutes=5),
        max_attempts=1,
    )

    assert failed.status == OutboxStatus.FAILED
