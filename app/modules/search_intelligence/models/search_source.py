from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel


class SearchSource(BaseModel):
    __tablename__ = "search_sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_search_sources_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_search_sources_workspace_product_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            "website_source_id",
            name="uq_search_sources_website_source_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_search_sources_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "integration_connection_id"],
            ["integration_connections.workspace_id", "integration_connections.id"],
            name="fk_search_sources_connection_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "integration_connection_id", "property_id"],
            [
                "search_provider_properties.workspace_id",
                "search_provider_properties.integration_connection_id",
                "search_provider_properties.id",
            ],
            name="fk_search_sources_property_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "website_source_id"],
            ["website_sources.workspace_id", "website_sources.product_id", "website_sources.id"],
            name="fk_search_sources_website_source_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('active', 'disconnected')", name="status_valid"),
        CheckConstraint(
            "(status = 'disconnected' AND disconnected_at IS NOT NULL) OR "
            "(status = 'active' AND disconnected_at IS NULL)",
            name="lifecycle_valid",
        ),
        Index(
            "uq_search_sources_active_product_provider",
            "workspace_id",
            "product_id",
            "provider",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    integration_connection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    property_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    website_source_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_successful_data_date: Mapped[date | None] = mapped_column(Date)


search_sources = SearchSource.__table__
__all__ = ["SearchSource", "search_sources"]
