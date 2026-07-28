from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


class ActionEvent(UuidPrimaryKeyMixin, Base):
    __tablename__ = "action_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "product_id", "id", name="uq_action_events_tenant_id"),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "action_id"],
            ["plan_actions.workspace_id", "plan_actions.product_id", "plan_actions.id"],
            name="fk_action_events_action_tenant",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    action_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
