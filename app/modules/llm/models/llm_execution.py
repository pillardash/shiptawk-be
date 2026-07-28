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


class LLMExecution(UuidPrimaryKeyMixin, Base):
    __tablename__ = "llm_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_llm_executions_product_tenant",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "product_id", "id", name="uq_llm_executions_tenant_id"),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "use_case",
            "idempotency_key",
            name="uq_llm_executions_tenant_use_case_idempotency",
        ),
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="llm_executions_status",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL AND "
            "lease_expires_at IS NOT NULL)",
            name="llm_executions_lease_triplet",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    use_case: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    route_name: Mapped[str] = mapped_column(String(100), nullable=False)
    route_snapshot: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    sanitized_metadata: Mapped[dict[str, object]] = mapped_column(
        json_type, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_fingerprint: Mapped[str | None] = mapped_column(String(64))
    validated_output_snapshot: Mapped[dict[str, object] | None] = mapped_column(json_type)
    failure_category: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
