from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel
from app.modules.products.enums.website_intelligence_enum import (
    WebsiteCrawlRunStatus,
    WebsiteCrawlTrigger,
)
from app.modules.products.models.website_source import website_json_type


class WebsiteCrawlRun(BaseModel):
    __tablename__ = "website_crawl_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_website_crawl_runs_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            name="uq_website_crawl_runs_workspace_product_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "source_id",
            "id",
            name="uq_website_crawl_runs_source_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "source_id",
            "idempotency_key",
            name="uq_website_crawl_runs_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_website_crawl_runs_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["website_sources.workspace_id", "website_sources.product_id", "website_sources.id"],
            name="fk_website_crawl_runs_source_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="crawl_status_valid",
        ),
        CheckConstraint("schema_version > 0", name="crawl_schema_version_positive"),
        CheckConstraint("page_limit > 0", name="crawl_page_limit_positive"),
        CheckConstraint(
            "pages_discovered >= 0 AND pages_succeeded >= 0 AND pages_failed >= 0",
            name="crawl_counts_nonnegative",
        ),
        CheckConstraint(
            "pages_succeeded + pages_failed <= pages_discovered",
            name="crawl_counts_valid",
        ),
        CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND error_code IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND error_code IS NULL) OR "
            "(status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(status = 'failed' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND error_code IS NOT NULL) OR "
            "(status = 'cancelled' AND completed_at IS NOT NULL AND error_code IS NULL)",
            name="crawl_lifecycle_valid",
        ),
        Index(
            "uq_website_crawl_runs_active_source",
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
    status: Mapped[WebsiteCrawlRunStatus] = mapped_column(
        SqlEnum(WebsiteCrawlRunStatus, native_enum=False, create_constraint=False, length=16),
        nullable=False,
        default=WebsiteCrawlRunStatus.pending,
        server_default=WebsiteCrawlRunStatus.pending.value,
    )
    trigger: Mapped[WebsiteCrawlTrigger] = mapped_column(
        SqlEnum(WebsiteCrawlTrigger, native_enum=False, create_constraint=False, length=16),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    page_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    pages_discovered: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    pages_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    pages_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    summary: Mapped[dict[str, object]] = mapped_column(
        website_json_type, nullable=False, default=dict, server_default="{}"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


website_crawl_runs = WebsiteCrawlRun.__table__

__all__ = ["WebsiteCrawlRun", "website_crawl_runs"]
