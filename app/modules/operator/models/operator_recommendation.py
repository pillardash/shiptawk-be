from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


class OperatorRecommendation(UuidPrimaryKeyMixin, Base):
    __tablename__ = "operator_recommendations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('recommendation','no_recommendation')", name="operator_recommendations_kind"
        ),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_operator_recommendations_tenant_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "opportunity_id",
            "idempotency_key",
            name="uq_operator_recommendations_opportunity_key",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            ondelete="CASCADE",
            name="fk_operator_recommendations_product_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "opportunity_id"],
            ["opportunities.workspace_id", "opportunities.product_id", "opportunities.id"],
            ondelete="RESTRICT",
            name="fk_operator_recommendations_opportunity_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.product_id", "operator_runs.id"],
            ondelete="RESTRICT",
            name="fk_operator_recommendations_operator_run_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "llm_execution_id"],
            ["llm_executions.workspace_id", "llm_executions.product_id", "llm_executions.id"],
            ondelete="RESTRICT",
            name="fk_operator_recommendations_llm_execution_tenant",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    opportunity_id: Mapped[UUID] = mapped_column(nullable=False)
    operator_run_id: Mapped[UUID] = mapped_column(nullable=False)
    llm_execution_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    output: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
