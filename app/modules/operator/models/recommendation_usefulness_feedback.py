from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin


class RecommendationUsefulnessFeedback(UuidPrimaryKeyMixin, Base):
    __tablename__ = "recommendation_usefulness_feedback"
    __table_args__ = (
        CheckConstraint(
            "schema_version = 1", name="recommendation_usefulness_feedback_schema_version"
        ),
        CheckConstraint(
            "rating IN ('useful','not_useful','unsure')",
            name="recommendation_usefulness_feedback_rating",
        ),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_recommendation_feedback_tenant_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "recommendation_id",
            "actor_id",
            name="uq_recommendation_feedback_actor",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "recommendation_id"],
            [
                "operator_recommendations.workspace_id",
                "operator_recommendations.product_id",
                "operator_recommendations.id",
            ],
            ondelete="CASCADE",
            name="fk_recommendation_feedback_recommendation_tenant",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    recommendation_id: Mapped[UUID] = mapped_column(nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    rating: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
