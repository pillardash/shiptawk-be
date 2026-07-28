from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


class ApprovalRequest(UuidPrimaryKeyMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name="approval_requests_status"
        ),
        CheckConstraint(
            "(asset_id IS NULL AND asset_revision IS NULL) OR "
            "(asset_id IS NOT NULL AND asset_revision IS NOT NULL)",
            name="approval_requests_asset_revision_pair",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_approval_requests_workspace_id_id"),
        UniqueConstraint("workspace_id", "action_id", name="uq_approval_requests_workspace_action"),
        ForeignKeyConstraint(
            ["workspace_id", "action_id"],
            ["plan_actions.workspace_id", "plan_actions.id"],
            name="fk_approval_requests_action_id_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "action_id"],
            ["plan_actions.workspace_id", "plan_actions.product_id", "plan_actions.id"],
            name="fk_approval_requests_action_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "asset_id"],
            ["prepared_assets.workspace_id", "prepared_assets.product_id", "prepared_assets.id"],
            name="fk_approval_requests_asset_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "asset_id", "asset_revision"],
            [
                "prepared_assets.workspace_id",
                "prepared_assets.product_id",
                "prepared_assets.id",
                "prepared_assets.revision",
            ],
            name="fk_approval_requests_exact_asset_revision",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    asset_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    asset_revision: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(Text)
    requested_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_by_actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
