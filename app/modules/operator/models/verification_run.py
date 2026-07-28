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
