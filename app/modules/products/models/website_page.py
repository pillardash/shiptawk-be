from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel
from app.modules.products.enums.website_intelligence_enum import WebsitePageStatus


class WebsitePage(BaseModel):
    __tablename__ = "website_pages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_website_pages_workspace_id_id"),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            name="uq_website_pages_workspace_product_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "source_id",
            "id",
            name="uq_website_pages_source_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "source_id",
            "canonical_url",
            name="uq_website_pages_canonical_url",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_website_pages_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["website_sources.workspace_id", "website_sources.product_id", "website_sources.id"],
            name="fk_website_pages_source_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "first_discovered_run_id"],
            [
                "website_crawl_runs.workspace_id",
                "website_crawl_runs.product_id",
                "website_crawl_runs.id",
            ],
            name="fk_website_pages_first_run_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('active', 'redirected', 'blocked', 'missing', 'failed')",
            name="page_status_valid",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    first_discovered_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[WebsitePageStatus] = mapped_column(
        SqlEnum(WebsitePageStatus, native_enum=False, create_constraint=False, length=16),
        nullable=False,
        default=WebsitePageStatus.active,
        server_default=WebsitePageStatus.active.value,
    )


website_pages = WebsitePage.__table__

__all__ = ["WebsitePage", "website_pages"]
