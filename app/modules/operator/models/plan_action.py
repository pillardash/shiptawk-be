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


class PlanAction(UuidPrimaryKeyMixin, Base):
    __tablename__ = "plan_actions"
    __table_args__ = (
        CheckConstraint(
            "approval_level IN ('safe', 'review', 'required')", name="plan_actions_approval_level"
        ),
        CheckConstraint(
            "approval_status IN ('not_required', 'pending', 'approved', 'rejected')",
            name="plan_actions_approval_status",
        ),
        CheckConstraint(
            "status IN ('pending', 'ready', 'executing', 'completed', "
            "'failed', 'cancelled', 'dismissed')",
            name="plan_actions_status",
        ),
        CheckConstraint("confidence IN ('low', 'medium', 'high')", name="plan_actions_confidence"),
        UniqueConstraint("workspace_id", "id", name="uq_plan_actions_workspace_id_id"),
        UniqueConstraint("workspace_id", "product_id", "id", name="uq_plan_actions_tenant_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_plan_actions_workspace_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "plan_id"],
            ["marketing_plans.workspace_id", "marketing_plans.id"],
            name="fk_plan_actions_plan_id_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "plan_id"],
            ["marketing_plans.workspace_id", "marketing_plans.product_id", "marketing_plans.id"],
            name="fk_plan_actions_plan_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "opportunity_id"],
            ["opportunities.workspace_id", "opportunities.id"],
            name="fk_plan_actions_opportunity_id_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_plan_actions_product_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "llm_execution_id"],
            ["llm_executions.workspace_id", "llm_executions.product_id", "llm_executions.id"],
            name="fk_plan_actions_llm_execution_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "recommendation_id"],
            [
                "operator_recommendations.workspace_id",
                "operator_recommendations.product_id",
                "operator_recommendations.id",
            ],
            name="fk_plan_actions_recommendation_tenant",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    llm_execution_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    plan_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    opportunity_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    missing_information: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    approval_level: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_status: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    cooldown_dedup_key: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    position: Mapped[int | None] = mapped_column(nullable=True)
    recommendation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    prepared_output_type: Mapped[str | None] = mapped_column(String(32))
    expected_metric: Mapped[dict[str, object] | None] = mapped_column(json_type)
    measurement_window_days: Mapped[int | None] = mapped_column(nullable=True)
    estimated_effort: Mapped[str | None] = mapped_column(String(16))
    approved_asset_id: Mapped[UUID | None] = mapped_column(nullable=True)
    approved_asset_revision: Mapped[int | None] = mapped_column(nullable=True)
    measurement_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by_actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_by_actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissal_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
