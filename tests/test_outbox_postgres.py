import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.outbox import (
    OutboxEvent,
    OutboxMetadata,
    claim_outbox_events,
    enqueue_outbox_event,
)

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")


async def test_concurrent_enqueue_replays_one_stable_event() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    key = f"concurrent-{uuid4()}"

    async def enqueue() -> tuple[bool, object]:
        async with sessions.begin() as db:
            result = await enqueue_outbox_event(
                db,
                event_name="test.concurrent",
                schema_version=1,
                idempotency_key=key,
                metadata=OutboxMetadata.model_validate({"key": key}),
            )
            return result.inserted, result.value.id

    outcomes = await asyncio.gather(enqueue(), enqueue())

    assert sorted(inserted for inserted, _ in outcomes) == [False, True]
    assert len({event_id for _, event_id in outcomes}) == 1
    async with sessions() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.event_name == "test.concurrent",
                OutboxEvent.idempotency_key == key,
            )
        )
        assert count == 1
    await engine.dispose()


async def test_concurrent_claims_skip_locked_events() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    event_name = f"test.claim.{uuid4()}"
    workspace_id = uuid4()
    async with sessions.begin() as db:
        for key in ("first", "second"):
            await enqueue_outbox_event(
                db,
                event_name=event_name,
                schema_version=1,
                idempotency_key=key,
                metadata=OutboxMetadata.model_validate({"key": key}),
                workspace_id=workspace_id,
            )

    first_claimed = asyncio.Event()
    release_first = asyncio.Event()

    async def hold_first_claim() -> object:
        async with sessions.begin() as db:
            claimed = await claim_outbox_events(
                db,
                limit=1,
                lease_duration=timedelta(minutes=1),
                workspace_id=workspace_id,
            )
            first_claimed.set()
            await release_first.wait()
            return claimed[0].id

    first_task = asyncio.create_task(hold_first_claim())
    await first_claimed.wait()
    async with sessions.begin() as db:
        second = await claim_outbox_events(
            db,
            limit=1,
            lease_duration=timedelta(minutes=1),
            workspace_id=workspace_id,
        )
    release_first.set()
    first_id = await first_task

    assert len(second) == 1
    assert second[0].id != first_id
    await engine.dispose()
