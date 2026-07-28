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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


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
