from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin


class ProductEvent(UuidPrimaryKeyMixin, Base):
    __tablename__ = "product_events"
    __table_args__ = (
        CheckConstraint("schema_version = 1", name="product_events_schema_version"),
        CheckConstraint("source = 'server'", name="product_events_source"),
        CheckConstraint(
            "(event_name IN ('github_installation_attached','repository_monitoring_enabled') "
            "AND product_id IS NULL) OR "
            "(event_name NOT IN ('github_installation_attached','repository_monitoring_enabled') "
            "AND product_id IS NOT NULL)",
            name="product_events_scope",
        ),
        CheckConstraint(
            "event_name IN ('product_created','github_installation_attached',"
            "'repository_monitoring_enabled',"
            "'product_profile_approved','website_connected','website_initial_crawl_completed',"
            "'search_console_connected','search_property_selected','search_baseline_viewed',"
            "'weekly_report_generated','weekly_report_viewed','recommendation_detail_viewed',"
            "'recommendation_feedback_submitted','recommendation_accepted','recommendation_dismissed',"
            "'asset_edited','asset_approved','asset_rejected','action_dismissed','action_started',"
            "'action_completed','measurement_viewed','measurement_completed','weekly_email_delivered',"
            "'compatibility_route_viewed')",
            name="product_events_event_name",
        ),
        CheckConstraint("length(idempotency_key) > 0", name="product_events_idempotency_key"),
        UniqueConstraint("workspace_id", "product_id", "id", name="uq_product_events_tenant_id"),
        UniqueConstraint(
            "workspace_id", "actor_id", "idempotency_key", name="uq_product_events_actor_key"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            ondelete="CASCADE",
            name="fk_product_events_product_tenant",
        ),
        Index(
            "ix_product_events_product_name_occurred",
            "workspace_id",
            "product_id",
            "event_name",
            "occurred_at",
        ),
        Index(
            "uq_product_events_actorless_key",
            "workspace_id",
            "event_name",
            "idempotency_key",
            unique=True,
            postgresql_where=text("actor_id IS NULL"),
            sqlite_where=text("actor_id IS NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[UUID | None] = mapped_column()
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="server", server_default="server"
    )
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[UUID | None] = mapped_column()
    resource_revision: Mapped[int | None] = mapped_column()
    report_ordinal: Mapped[int | None] = mapped_column()
    second_report_return: Mapped[bool | None] = mapped_column()
    fourth_report_return: Mapped[bool | None] = mapped_column()
    valid_no_recommendation_report: Mapped[bool | None] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
