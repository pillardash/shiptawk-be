from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel


class SearchProviderProperty(BaseModel):
    __tablename__ = "search_provider_properties"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="uq_search_provider_properties_workspace_id_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "integration_connection_id",
            "id",
            name="uq_search_provider_properties_connection_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "integration_connection_id",
            "provider_property_id",
            name="uq_search_provider_properties_connection_property",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "integration_connection_id"],
            ["integration_connections.workspace_id", "integration_connections.id"],
            name="fk_search_provider_properties_connection_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint("property_type IN ('domain', 'url_prefix')", name="property_type_valid"),
        CheckConstraint(
            "compatibility_status IN ('compatible', 'incompatible', 'unknown')",
            name="compatibility_valid",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    integration_connection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_property_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    property_type: Mapped[str] = mapped_column(String(16), nullable=False)
    permission_level: Mapped[str] = mapped_column(String(64), nullable=False)
    compatibility_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="unknown"
    )
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


search_provider_properties = SearchProviderProperty.__table__
__all__ = ["SearchProviderProperty", "search_provider_properties"]
