from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
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
            "status IN ('running', 'completed', 'failed', 'stopped')", name="operator_runs_status"
        ),
        CheckConstraint("length(idempotency_key) > 0", name="operator_runs_idempotency_key"),
        UniqueConstraint("workspace_id", "id", name="uq_operator_runs_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_operator_runs_workspace_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_operator_runs_product_id_tenant",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False, default=dict)
    summary: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class Opportunity(UuidPrimaryKeyMixin, Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        CheckConstraint("confidence IN ('low', 'medium', 'high')", name="opportunities_confidence"),
        CheckConstraint(
            "approval_level IN ('safe', 'review', 'required')",
            name="opportunities_approval_level",
        ),
        CheckConstraint(
            "status IN ('open', 'planned', 'completed', 'dismissed')", name="opportunities_status"
        ),
        CheckConstraint("impact_score BETWEEN 0 AND 100", name="opportunities_impact_score"),
        CheckConstraint("effort_score BETWEEN 0 AND 100", name="opportunities_effort_score"),
        CheckConstraint("urgency_score BETWEEN 0 AND 100", name="opportunities_urgency_score"),
        CheckConstraint("length(cooldown_dedup_key) > 0", name="opportunities_cooldown_key"),
        UniqueConstraint("workspace_id", "id", name="uq_opportunities_workspace_id_id"),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_opportunities_product_id_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.id"],
            name="fk_opportunities_operator_run_id_tenant",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    operator_run_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    interpretation: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    missing_information: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    approval_level: Mapped[str] = mapped_column(String(16), nullable=False)
    measurement_window: Mapped[str] = mapped_column(String(64), nullable=False)
    stop_conditions: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    impact_score: Mapped[int] = mapped_column(Integer, nullable=False)
    effort_score: Mapped[int] = mapped_column(Integer, nullable=False)
    urgency_score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    cooldown_dedup_key: Mapped[str] = mapped_column(String(255), nullable=False)
    cooldown_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MarketingPlan(UuidPrimaryKeyMixin, Base):
    __tablename__ = "marketing_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'completed', 'cancelled')", name="marketing_plans_status"
        ),
        CheckConstraint("period_end > period_start", name="marketing_plans_period"),
        CheckConstraint("length(idempotency_key) > 0", name="marketing_plans_idempotency_key"),
        UniqueConstraint("workspace_id", "id", name="uq_marketing_plans_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_marketing_plans_workspace_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.id"],
            name="fk_marketing_plans_operator_run_id_tenant",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operator_run_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


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
            "status IN ('pending', 'ready', 'executing', 'completed', 'failed', 'cancelled')",
            name="plan_actions_status",
        ),
        CheckConstraint("confidence IN ('low', 'medium', 'high')", name="plan_actions_confidence"),
        UniqueConstraint("workspace_id", "id", name="uq_plan_actions_workspace_id_id"),
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
            ["workspace_id", "opportunity_id"],
            ["opportunities.workspace_id", "opportunities.id"],
            name="fk_plan_actions_opportunity_id_tenant",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ApprovalRequest(UuidPrimaryKeyMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name="approval_requests_status"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_approval_requests_workspace_id_id"),
        UniqueConstraint("workspace_id", "action_id", name="uq_approval_requests_workspace_action"),
        ForeignKeyConstraint(
            ["workspace_id", "action_id"],
            ["plan_actions.workspace_id", "plan_actions.id"],
            name="fk_approval_requests_action_id_tenant",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
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


class ExecutionRun(UuidPrimaryKeyMixin, Base):
    __tablename__ = "execution_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'blocked')", name="execution_runs_status"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_execution_runs_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_execution_runs_workspace_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "action_id"],
            ["plan_actions.workspace_id", "plan_actions.id"],
            name="fk_execution_runs_action_id_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "approval_request_id"],
            ["approval_requests.workspace_id", "approval_requests.id"],
            name="fk_execution_runs_approval_request_id_tenant",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    approval_request_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    output: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class VerificationRun(UuidPrimaryKeyMixin, Base):
    __tablename__ = "verification_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'passed', 'failed')", name="verification_runs_status"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_verification_runs_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_verification_runs_workspace_idempotency"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "execution_run_id"],
            ["execution_runs.workspace_id", "execution_runs.id"],
            name="fk_verification_runs_execution_run_id_tenant",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    execution_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeasurementWindow(UuidPrimaryKeyMixin, Base):
    __tablename__ = "measurement_windows"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'measuring', 'completed', 'cancelled')",
            name="measurement_windows_status",
        ),
        CheckConstraint("ends_at > starts_at", name="measurement_windows_period"),
        UniqueConstraint("workspace_id", "id", name="uq_measurement_windows_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_measurement_windows_workspace_idempotency"
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
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metric_contract: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    baseline: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(json_type)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
