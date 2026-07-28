from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKeyConstraint, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


class LLMExecutionAttempt(UuidPrimaryKeyMixin, Base):
    __tablename__ = "llm_execution_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "execution_id"],
            ["llm_executions.workspace_id", "llm_executions.product_id", "llm_executions.id"],
            name="fk_llm_execution_attempts_execution_tenant",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "execution_id",
            "attempt_number",
            name="uq_llm_execution_attempts_execution_number",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    execution_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    lease_token: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    finish_reason: Mapped[str | None] = mapped_column(String(64))
    refusal: Mapped[bool] = mapped_column(nullable=False, default=False)
    usage: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False, default=dict)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    pricing_version: Mapped[str] = mapped_column(String(64), nullable=False)
    latency_ms: Mapped[int | None]
    error_category: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
