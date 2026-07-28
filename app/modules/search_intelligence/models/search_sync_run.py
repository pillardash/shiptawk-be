from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel


class SearchSyncRun(BaseModel):
    __tablename__ = "search_sync_runs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "product_id", "source_id", "id", name="uq_search_sync_runs_source_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "source_id",
            "idempotency_key",
            name="uq_search_sync_runs_source_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["search_sources.workspace_id", "search_sources.product_id", "search_sources.id"],
            name="fk_search_sync_runs_source_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint("trigger IN ('initial', 'scheduled', 'manual')", name="trigger_valid"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("requested_start_date <= requested_end_date", name="date_range_valid"),
        CheckConstraint("policy_version <> '' AND schema_version > 0", name="versions_valid"),
        CheckConstraint("rows_received >= 0 AND rows_written >= 0", name="counts_nonnegative"),
        CheckConstraint(
            "progress_date IS NULL OR (progress_date >= requested_start_date "
            "AND progress_date <= requested_end_date)",
            name="progress_valid",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="lease_valid",
        ),
        Index(
            "uq_search_sync_runs_active_source",
            "workspace_id",
            "source_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
            sqlite_where=text("status IN ('pending', 'running')"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    progress_date: Mapped[date | None] = mapped_column(Date)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    rows_received: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_category: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


search_sync_runs = SearchSyncRun.__table__
__all__ = ["SearchSyncRun", "search_sync_runs"]
