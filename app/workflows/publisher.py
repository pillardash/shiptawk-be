from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID

import inngest
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integrations.models import github_raw_event_consumers


@dataclass(frozen=True, slots=True)
class MetadataEvent:
    name: str
    event_id: str
    data: dict[str, object]


class MetadataEventPublisher(Protocol):
    async def publish(self, event: MetadataEvent) -> None: ...


class InngestMetadataEventPublisher:
    def __init__(self, client: inngest.Inngest) -> None:
        self._client = client

    async def publish(self, event: MetadataEvent) -> None:
        await self._client.send(
            inngest.Event(name=event.name, id=event.event_id, data=cast(Any, event.data))
        )


async def publish_pending_consumers(
    db: AsyncSession,
    *,
    publisher: MetadataEventPublisher,
    raw_event_id: UUID | None,
) -> None:
    statement = select(github_raw_event_consumers.c.id).where(
        github_raw_event_consumers.c.processing_state.in_(["pending", "failed"])
    )
    if raw_event_id is not None:
        statement = statement.where(github_raw_event_consumers.c.raw_event_id == raw_event_id)
    rows = (await db.execute(statement)).all()
    for row in rows:
        claimed = await db.execute(
            update(github_raw_event_consumers)
            .where(
                github_raw_event_consumers.c.id == row.id,
                github_raw_event_consumers.c.processing_state.in_(["pending", "failed"]),
            )
            .values(
                processing_state="enqueued",
                publish_attempts=github_raw_event_consumers.c.publish_attempts + 1,
                publish_error_category=None,
                updated_at=func.now(),
            )
        )
        await db.commit()
        if cast(CursorResult[object], claimed).rowcount != 1:
            continue
        identifier = str(row.id)
        try:
            await publisher.publish(
                MetadataEvent(
                    name="github/event.received",
                    event_id=identifier,
                    data={
                        "consumerId": identifier,
                        "schemaVersion": 1,
                        "correlationId": identifier,
                    },
                )
            )
        except Exception:
            await db.execute(
                update(github_raw_event_consumers)
                .where(github_raw_event_consumers.c.id == row.id)
                .values(
                    processing_state="failed",
                    publish_error_category="transport",
                    updated_at=func.now(),
                )
            )
            await db.commit()
            raise
