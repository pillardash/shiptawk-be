from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


class ActionRiskReview(UuidPrimaryKeyMixin, Base):
    __tablename__ = "action_risk_reviews"
    __table_args__ = (
        CheckConstraint(
            "final_outcome IN ('pass','review','block')", name="action_risk_reviews_outcome"
        ),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_action_risk_reviews_tenant_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "asset_id",
            "asset_revision",
            name="uq_action_risk_reviews_asset_revision",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            ondelete="CASCADE",
            name="fk_action_risk_reviews_product_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "asset_id"],
            ["prepared_assets.workspace_id", "prepared_assets.product_id", "prepared_assets.id"],
            ondelete="RESTRICT",
            name="fk_action_risk_reviews_asset_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "llm_execution_id"],
            ["llm_executions.workspace_id", "llm_executions.product_id", "llm_executions.id"],
            ondelete="RESTRICT",
            name="fk_action_risk_reviews_llm_execution_tenant",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    asset_id: Mapped[UUID] = mapped_column(nullable=False)
    asset_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_execution_id: Mapped[UUID] = mapped_column(nullable=False)
    deterministic_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    ai_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    final_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    findings: Mapped[list[dict[str, object]]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
