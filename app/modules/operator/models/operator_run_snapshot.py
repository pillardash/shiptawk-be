from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


class OperatorRunSnapshot(UuidPrimaryKeyMixin, Base):
    __tablename__ = "operator_run_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_operator_run_snapshots_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_operator_run_snapshots_tenant_id"
        ),
        UniqueConstraint(
            "workspace_id", "product_id", "operator_run_id", name="uq_operator_run_snapshots_run"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_operator_run_snapshots_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.product_id", "operator_runs.id"],
            name="fk_operator_run_snapshots_run_tenant",
            ondelete="CASCADE",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    operator_run_id: Mapped[UUID] = mapped_column(nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_payload: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
