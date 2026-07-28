from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKeyConstraint, Text, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel
from app.modules.products.enums.website_intelligence_enum import WebsiteSourceStatus

website_json_type = JSONB().with_variant(JSON(), "sqlite")


class WebsiteSource(BaseModel):
    __tablename__ = "website_sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_website_sources_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            name="uq_website_sources_workspace_product_id",
        ),
        UniqueConstraint("workspace_id", "product_id", name="uq_website_sources_product"),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_website_sources_product_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'disconnected')",
            name="source_status_valid",
        ),
        CheckConstraint(
            "(status = 'disconnected' AND disconnected_at IS NOT NULL) OR "
            "(status <> 'disconnected' AND disconnected_at IS NULL)",
            name="source_lifecycle_valid",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_base_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[WebsiteSourceStatus] = mapped_column(
        SqlEnum(WebsiteSourceStatus, native_enum=False, create_constraint=False, length=16),
        nullable=False,
        default=WebsiteSourceStatus.active,
        server_default=WebsiteSourceStatus.active.value,
    )
    sitemap_urls: Mapped[list[str]] = mapped_column(
        website_json_type, nullable=False, default=list, server_default="[]"
    )
    blog_url: Mapped[str | None] = mapped_column(Text)
    documentation_url: Mapped[str | None] = mapped_column(Text)
    last_successful_crawl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


website_sources = WebsiteSource.__table__

__all__ = ["WebsiteSource", "website_sources"]
