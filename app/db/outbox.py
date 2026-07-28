"""Transactional outbox persistence and leasing primitives."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast

from pydantic import RootModel, field_validator
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    or_,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.db.core_tables import json_type
from app.db.idempotency import InsertReplayResult, insert_or_replay

type MetadataScalar = str | int | float | bool | None
type MetadataValue = MetadataScalar | list[MetadataScalar]
_SENSITIVE_KEY_PARTS = (
    "body",
    "content",
    "credential",
    "password",
    "payload",
    "prompt",
    "secret",
    "source",
    "token",
)


class OutboxMetadata(RootModel[dict[str, MetadataValue]]):
    """Small pointer-style event metadata; never unrestricted content."""

    @field_validator("root")
    @classmethod
    def validate_metadata(cls, value: dict[str, MetadataValue]) -> dict[str, MetadataValue]:
        for key, item in value.items():
            normalized_key = key.lower().replace("_", "").replace("-", "")
            is_identifier = normalized_key.endswith("id")
            if not is_identifier and any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                raise ValueError(f"metadata key is not allowed: {key}")
            items = item if isinstance(item, list) else [item]
            if any(isinstance(entry, str) and len(entry) > 2048 for entry in items):
                raise ValueError("metadata string values must not exceed 2048 characters")
        return value


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "event_name", "idempotency_key", name="uq_outbox_events_event_idempotency"
        ),
        CheckConstraint("schema_version > 0", name="schema_version_positive"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'failed')",
            name="status_valid",
        ),
        CheckConstraint(
            "(status = 'publishing' AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'publishing' AND lease_expires_at IS NULL)",
            name="lease_matches_status",
        ),
        Index("ix_outbox_events_claim", "status", "available_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    event_name: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    metadata_payload: Mapped[dict[str, MetadataValue]] = mapped_column(json_type, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        String(16), nullable=False, default=OutboxStatus.PENDING, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("now()"),
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("now()"),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_category: Mapped[str | None] = mapped_column(String(64))


outbox_events = OutboxEvent.__table__


async def enqueue_outbox_event(
    db: AsyncSession,
    *,
    event_name: str,
    schema_version: int,
    idempotency_key: str,
    metadata: OutboxMetadata,
    event_id: uuid.UUID | None = None,
    aggregate_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    available_at: datetime | None = None,
) -> InsertReplayResult[OutboxEvent]:
    """Enqueue in the caller's transaction, replaying the stable event on duplicates."""
    values = {
        "id": event_id or uuid.uuid4(),
        "event_name": event_name,
        "schema_version": schema_version,
        "idempotency_key": idempotency_key,
        "aggregate_id": aggregate_id,
        "workspace_id": workspace_id,
        "product_id": product_id,
        "metadata_payload": metadata.model_dump(),
        "status": OutboxStatus.PENDING,
        "attempts": 0,
        "available_at": available_at or datetime.now(UTC),
        "created_at": datetime.now(UTC),
    }
    result = await insert_or_replay(
        db,
        cast(Table, OutboxEvent.__table__),
        values=values,
        conflict_columns=("event_name", "idempotency_key"),
    )
    event = await db.get(OutboxEvent, result.value["id"], populate_existing=True)
    if event is None:
        raise RuntimeError("outbox insert succeeded without a readable event")
    return InsertReplayResult(value=event, inserted=result.inserted)


async def claim_outbox_events(
    db: AsyncSession,
    *,
    limit: int,
    lease_duration: timedelta,
    now: datetime | None = None,
    workspace_id: uuid.UUID | None = None,
) -> list[OutboxEvent]:
    """Lease an available batch using row locks; delivery remains at least once."""
    if limit < 1 or lease_duration <= timedelta(0):
        raise ValueError("limit and lease_duration must be positive")
    claimed_at = now or datetime.now(UTC)
    statement = (
        select(OutboxEvent)
        .where(
            OutboxEvent.available_at <= claimed_at,
            or_(
                OutboxEvent.status == OutboxStatus.PENDING,
                (
                    (OutboxEvent.status == OutboxStatus.PUBLISHING)
                    & (OutboxEvent.lease_expires_at <= claimed_at)
                ),
            ),
        )
        .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if workspace_id is not None:
        statement = statement.where(OutboxEvent.workspace_id == workspace_id)
    events = list((await db.scalars(statement)).all())
    for event in events:
        event.status = OutboxStatus.PUBLISHING
        event.lease_expires_at = claimed_at + lease_duration
        event.attempts += 1
    await db.flush()
    return events


async def mark_outbox_event_published(
    db: AsyncSession, event_id: uuid.UUID, *, published_at: datetime | None = None
) -> OutboxEvent:
    event = await _publishing_event(db, event_id)
    event.status = OutboxStatus.PUBLISHED
    event.published_at = published_at or datetime.now(UTC)
    event.lease_expires_at = None
    event.last_error_category = None
    await db.flush()
    return event


async def release_outbox_event_for_retry(
    db: AsyncSession,
    event_id: uuid.UUID,
    *,
    error_category: str,
    available_at: datetime,
    max_attempts: int = 10,
) -> OutboxEvent:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    event = await _publishing_event(db, event_id)
    event.status = OutboxStatus.FAILED if event.attempts >= max_attempts else OutboxStatus.PENDING
    event.available_at = available_at
    event.lease_expires_at = None
    event.last_error_category = _sanitize_error_category(error_category)
    await db.flush()
    return event


async def _publishing_event(db: AsyncSession, event_id: uuid.UUID) -> OutboxEvent:
    event = await db.get(OutboxEvent, event_id)
    if event is None or event.status != OutboxStatus.PUBLISHING:
        raise ValueError("outbox event is not currently publishing")
    return event


def _sanitize_error_category(category: str) -> str:
    category_prefix = category.split(":", maxsplit=1)[0]
    sanitized = re.sub(r"[^a-z0-9]+", "_", category_prefix.lower()).strip("_")[:64]
    return sanitized or "unknown"
