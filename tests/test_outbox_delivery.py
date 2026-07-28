from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.outbox import OutboxEvent, OutboxMetadata, OutboxStatus, enqueue_outbox_event
from app.workflows.outbox import OutboxDeliveryWorkflow
from app.workflows.publisher import MetadataEvent


@pytest.fixture
async def sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield session_factory
    await engine.dispose()


@dataclass
class RecordingPublisher:
    fail: bool = False
    events: list[MetadataEvent] = field(default_factory=list)

    async def publish(self, event: MetadataEvent) -> None:
        self.events.append(event)
        if self.fail:
            raise RuntimeError("provider response containing secret detail")


async def _seed_event(sessions: async_sessionmaker[AsyncSession]) -> OutboxEvent:
    async with sessions.begin() as db:
        result = await enqueue_outbox_event(
            db,
            event_name="website/crawl.requested",
            schema_version=1,
            idempotency_key="crawl-run",
            metadata=OutboxMetadata.model_validate(
                {
                    "runId": "run-1",
                    "sourceId": "source-1",
                    "schemaVersion": 1,
                }
            ),
        )
        return result.value


async def test_delivery_publishes_metadata_with_stable_outbox_id_and_marks_success(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    event = await _seed_event(sessions)
    publisher = RecordingPublisher()
    workflow = OutboxDeliveryWorkflow(sessions=sessions, publisher=publisher)

    result = await workflow.deliver()

    assert result == {"claimed": 1, "published": 1, "retried": 0, "failed": 0}
    assert publisher.events == [
        MetadataEvent(
            name="website/crawl.requested",
            event_id=str(event.id),
            data={"runId": "run-1", "sourceId": "source-1", "schemaVersion": 1},
        )
    ]
    async with sessions() as db:
        stored = await db.scalar(select(OutboxEvent).where(OutboxEvent.id == event.id))
        assert stored is not None
        assert stored.status == OutboxStatus.PUBLISHED
        assert stored.published_at is not None


async def test_delivery_reschedules_transport_failure_without_storing_exception_detail(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    event = await _seed_event(sessions)
    publisher = RecordingPublisher(fail=True)
    now = datetime.now(UTC)
    workflow = OutboxDeliveryWorkflow(sessions=sessions, publisher=publisher)

    result = await workflow.deliver(now=now)

    assert result == {"claimed": 1, "published": 0, "retried": 1, "failed": 0}
    async with sessions() as db:
        stored = await db.scalar(select(OutboxEvent).where(OutboxEvent.id == event.id))
        assert stored is not None
        assert stored.status == OutboxStatus.PENDING
        assert stored.available_at.replace(tzinfo=UTC) > now
        assert stored.last_error_category == "transport"
