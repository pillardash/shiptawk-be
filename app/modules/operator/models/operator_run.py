from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


class OperatorRun(UuidPrimaryKeyMixin, Base):
    __tablename__ = "operator_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'significant_event')", name="operator_runs_trigger"
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'stopped')",
            name="operator_runs_status",
        ),
        CheckConstraint(
            "run_kind IN ('legacy_planning', 'opportunity_detection', 'weekly_growth')",
            name="operator_runs_run_kind",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="operator_runs_lease_pair",
        ),
        CheckConstraint("length(idempotency_key) > 0", name="operator_runs_idempotency_key"),
        UniqueConstraint("workspace_id", "id", name="uq_operator_runs_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_operator_runs_workspace_product_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "idempotency_key",
            name="uq_operator_runs_workspace_product_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_operator_runs_product_id_tenant",
        ),
        Index(
            "uq_operator_runs_weekly_period",
            "workspace_id",
            "product_id",
            "logical_period_start",
            "workflow_version",
            unique=True,
            postgresql_where="run_kind = 'weekly_growth' AND logical_period_start IS NOT NULL",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    run_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="legacy_planning", server_default="legacy_planning"
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    input_fingerprint: Mapped[str | None] = mapped_column(String(64))
    input_schema_version: Mapped[str | None] = mapped_column(String(64))
    detector_suite_version: Mapped[str | None] = mapped_column(String(64))
    quality_policy_version: Mapped[str | None] = mapped_column(String(64))
    scoring_policy_version: Mapped[str | None] = mapped_column(String(64))
    cooldown_policy_version: Mapped[str | None] = mapped_column(String(64))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_category: Mapped[str | None] = mapped_column(String(64))
    logical_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    logical_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    workflow_version: Mapped[str | None] = mapped_column(String(64))
    current_stage: Mapped[str | None] = mapped_column(String(32))
    stage_summary: Mapped[dict[str, object] | None] = mapped_column(json_type)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_reason: Mapped[str | None] = mapped_column(String(500))
    analyzer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False, default=dict)
    summary: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
