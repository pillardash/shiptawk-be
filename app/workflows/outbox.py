"""Durable delivery from the transactional outbox to Inngest."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.outbox import (
    OutboxStatus,
    claim_outbox_events,
    mark_outbox_event_published,
    release_outbox_event_for_retry,
)
from app.workflows.publisher import MetadataEvent, MetadataEventPublisher


class OutboxDeliveryWorkflow:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        publisher: MetadataEventPublisher,
        batch_size: int = 100,
        max_attempts: int = 10,
    ) -> None:
        self._sessions = sessions
        self._publisher = publisher
        self._batch_size = batch_size
        self._max_attempts = max_attempts

    async def deliver(self, *, now: datetime | None = None) -> dict[str, int]:
        delivered_at = now or datetime.now(UTC)
        async with self._sessions.begin() as db:
            events = await claim_outbox_events(
                db,
                limit=self._batch_size,
                lease_duration=timedelta(minutes=2),
                now=delivered_at,
            )

        published = 0
        retried = 0
        failed = 0
        for event in events:
            try:
                await self._publisher.publish(
                    MetadataEvent(
                        name=event.event_name,
                        event_id=str(event.id),
                        data=dict(event.metadata_payload),
                    )
                )
            except Exception:
                delay_seconds = min(300, 2 ** min(event.attempts, 8))
                async with self._sessions.begin() as db:
                    released = await release_outbox_event_for_retry(
                        db,
                        event.id,
                        error_category="transport",
                        available_at=delivered_at + timedelta(seconds=delay_seconds),
                        max_attempts=self._max_attempts,
                    )
                if released.status == OutboxStatus.FAILED:
                    failed += 1
                else:
                    retried += 1
                continue

            async with self._sessions.begin() as db:
                await mark_outbox_event_published(db, event.id, published_at=delivered_at)
            published += 1

        return {
            "claimed": len(events),
            "published": published,
            "retried": retried,
            "failed": failed,
        }
