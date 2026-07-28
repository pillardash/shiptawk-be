from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel
from app.modules.products.enums.website_intelligence_enum import WebsiteCapabilityMappingStatus
from app.modules.products.models.website_source import website_json_type


class WebsiteCapabilityMapping(BaseModel):
    __tablename__ = "website_capability_mappings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "crawl_run_id",
            "capability_key",
            name="uq_website_capability_mappings_run_capability",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_website_capability_mappings_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["website_sources.workspace_id", "website_sources.product_id", "website_sources.id"],
            name="fk_website_capability_mappings_source_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "crawl_run_id"],
            [
                "website_crawl_runs.workspace_id",
                "website_crawl_runs.product_id",
                "website_crawl_runs.id",
            ],
            name="fk_website_capability_mappings_run_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "page_id"],
            ["website_pages.workspace_id", "website_pages.product_id", "website_pages.id"],
            name="fk_website_capability_mappings_page_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('represented', 'partial', 'missing')",
            name="coverage_status_valid",
        ),
        CheckConstraint(
            "relevance_score BETWEEN 0 AND 100 AND confidence_score BETWEEN 0 AND 100",
            name="mapping_scores_valid",
        ),
        CheckConstraint(
            "(status IN ('represented', 'partial') AND page_id IS NOT NULL) OR "
            "(status = 'missing' AND page_id IS NULL)",
            name="mapping_page_lifecycle_valid",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    crawl_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    page_id: Mapped[UUID | None] = mapped_column(index=True)
    capability_key: Mapped[str] = mapped_column(String(128), nullable=False)
    capability_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[WebsiteCapabilityMappingStatus] = mapped_column(
        SqlEnum(
            WebsiteCapabilityMappingStatus,
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        nullable=False,
    )
    relevance_score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(
        website_json_type, nullable=False, default=dict, server_default="{}"
    )
    mapping_version: Mapped[str] = mapped_column(String(64), nullable=False)


website_capability_mappings = WebsiteCapabilityMapping.__table__

__all__ = ["WebsiteCapabilityMapping", "website_capability_mappings"]
