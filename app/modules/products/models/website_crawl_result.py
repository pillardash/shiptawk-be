from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel
from app.modules.products.enums.website_intelligence_enum import WebsiteCrawlResultStatus
from app.modules.products.models.website_source import website_json_type


class WebsiteCrawlResult(BaseModel):
    __tablename__ = "website_crawl_results"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_website_crawl_results_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            name="uq_website_crawl_results_workspace_product_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "crawl_run_id",
            "page_id",
            name="uq_website_crawl_results_run_page",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_website_crawl_results_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["website_sources.workspace_id", "website_sources.product_id", "website_sources.id"],
            name="fk_website_crawl_results_source_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "crawl_run_id"],
            [
                "website_crawl_runs.workspace_id",
                "website_crawl_runs.product_id",
                "website_crawl_runs.id",
            ],
            name="fk_website_crawl_results_run_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "page_id"],
            ["website_pages.workspace_id", "website_pages.product_id", "website_pages.id"],
            name="fk_website_crawl_results_page_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'blocked', 'failed', 'skipped')",
            name="result_status_valid",
        ),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="result_http_status_valid",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND fetched_at IS NOT NULL AND http_status IS NOT NULL "
            "AND error_code IS NULL AND content_fingerprint IS NOT NULL) OR "
            "(status = 'blocked' AND fetched_at IS NOT NULL AND error_code IS NOT NULL) OR "
            "(status = 'failed' AND fetched_at IS NOT NULL AND error_code IS NOT NULL) OR "
            "(status = 'skipped' AND fetched_at IS NULL AND http_status IS NULL "
            "AND error_code IS NOT NULL)",
            name="result_lifecycle_valid",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    crawl_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    page_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    status: Mapped[WebsiteCrawlResultStatus] = mapped_column(
        SqlEnum(WebsiteCrawlResultStatus, native_enum=False, create_constraint=False, length=16),
        nullable=False,
    )
    final_url: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    meta_description: Mapped[str | None] = mapped_column(Text)
    headings: Mapped[list[dict[str, object]]] = mapped_column(
        website_json_type, nullable=False, default=list, server_default="[]"
    )
    extracted_text: Mapped[str | None] = mapped_column(Text)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    is_indexable: Mapped[bool | None] = mapped_column(Boolean)
    indexability_reasons: Mapped[list[str]] = mapped_column(
        website_json_type, nullable=False, default=list, server_default="[]"
    )
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


website_crawl_results = WebsiteCrawlResult.__table__

__all__ = ["WebsiteCrawlResult", "website_crawl_results"]
