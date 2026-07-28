from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin


class OpportunityStatusEvent(UuidPrimaryKeyMixin, Base):
    __tablename__ = "opportunity_status_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_opportunity_status_events_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_opportunity_status_events_tenant_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_opportunity_status_events_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "opportunity_id"],
            ["opportunities.workspace_id", "opportunities.product_id", "opportunities.id"],
            name="fk_opportunity_status_events_opportunity_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "feedback_id"],
            [
                "opportunity_feedback.workspace_id",
                "opportunity_feedback.product_id",
                "opportunity_feedback.id",
            ],
            name="fk_opportunity_status_events_feedback_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_opportunity_status_events_opportunity",
            "workspace_id",
            "product_id",
            "opportunity_id",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    opportunity_id: Mapped[UUID] = mapped_column(nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    previous_status: Mapped[str] = mapped_column(String(20), nullable=False)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    feedback_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
