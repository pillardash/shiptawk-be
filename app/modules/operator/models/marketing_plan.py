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


class MarketingPlan(UuidPrimaryKeyMixin, Base):
    __tablename__ = "marketing_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'cancelled', 'preparing', "
            "'ready', 'completed', 'stopped')",
            name="marketing_plans_status",
        ),
        CheckConstraint("period_end > period_start", name="marketing_plans_period"),
        CheckConstraint("length(idempotency_key) > 0", name="marketing_plans_idempotency_key"),
        UniqueConstraint("workspace_id", "id", name="uq_marketing_plans_workspace_id_id"),
        UniqueConstraint("workspace_id", "product_id", "id", name="uq_marketing_plans_tenant_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_marketing_plans_workspace_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.id"],
            name="fk_marketing_plans_operator_run_id_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.product_id", "operator_runs.id"],
            name="fk_marketing_plans_run_product_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_marketing_plans_product_tenant",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    operator_run_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    plan_revision: Mapped[int | None] = mapped_column(nullable=True)
    schema_version: Mapped[int | None] = mapped_column(nullable=True)
    selection_policy_version: Mapped[str | None] = mapped_column(String(64))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_snapshot: Mapped[dict[str, object] | None] = mapped_column(json_type)
    action_count: Mapped[int | None] = mapped_column(nullable=True)
    no_recommendation_count: Mapped[int | None] = mapped_column(nullable=True)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
