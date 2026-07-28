from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
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


class MeasurementWindow(UuidPrimaryKeyMixin, Base):
    __tablename__ = "measurement_windows"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'measuring', 'completed', 'cancelled', 'unavailable')",
            name="measurement_windows_status",
        ),
        CheckConstraint("ends_at > starts_at", name="measurement_windows_period"),
        UniqueConstraint("workspace_id", "id", name="uq_measurement_windows_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_measurement_windows_workspace_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "action_id"],
            ["plan_actions.workspace_id", "plan_actions.product_id", "plan_actions.id"],
            name="fk_measurement_windows_action_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "action_id"],
            ["plan_actions.workspace_id", "plan_actions.id"],
            name="fk_measurement_windows_action_id_tenant",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metric_contract: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    baseline: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(json_type)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    expected_metric: Mapped[dict[str, object] | None] = mapped_column(json_type)
    baseline_period: Mapped[dict[str, object] | None] = mapped_column(json_type)
    baseline_value: Mapped[dict[str, object] | None] = mapped_column(json_type)
    comparison_period: Mapped[dict[str, object] | None] = mapped_column(json_type)
    comparison_value: Mapped[dict[str, object] | None] = mapped_column(json_type)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(32))
    unavailable_reason: Mapped[str | None] = mapped_column(String(500))
    limitations: Mapped[list[str] | None] = mapped_column(json_type)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
