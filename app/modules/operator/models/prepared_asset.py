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


class PreparedAsset(UuidPrimaryKeyMixin, Base):
    __tablename__ = "prepared_assets"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('seo_page_update','blog_brief','product_update','x_draft','no_asset')",
            name="prepared_assets_kind",
        ),
        CheckConstraint("revision > 0", name="prepared_assets_revision"),
        UniqueConstraint("workspace_id", "product_id", "id", name="uq_prepared_assets_tenant_id"),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "recommendation_id",
            "revision",
            name="uq_prepared_assets_recommendation_revision",
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            "revision",
            name="uq_prepared_assets_tenant_id_revision",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            "revision",
            name="uq_prepared_assets_workspace_id_revision",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            ondelete="CASCADE",
            name="fk_prepared_assets_product_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "recommendation_id"],
            [
                "operator_recommendations.workspace_id",
                "operator_recommendations.product_id",
                "operator_recommendations.id",
            ],
            ondelete="RESTRICT",
            name="fk_prepared_assets_recommendation_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "action_id"],
            ["plan_actions.workspace_id", "plan_actions.product_id", "plan_actions.id"],
            ondelete="RESTRICT",
            name="fk_prepared_assets_action_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "llm_execution_id"],
            ["llm_executions.workspace_id", "llm_executions.product_id", "llm_executions.id"],
            ondelete="RESTRICT",
            name="fk_prepared_assets_llm_execution_tenant",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    recommendation_id: Mapped[UUID] = mapped_column(nullable=False)
    action_id: Mapped[UUID | None] = mapped_column()
    llm_execution_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    structured_content: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
