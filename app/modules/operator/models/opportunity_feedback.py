from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin


class OpportunityFeedback(UuidPrimaryKeyMixin, Base):
    __tablename__ = "opportunity_feedback"
    __table_args__ = (
        CheckConstraint(
            "usefulness IS NULL OR usefulness BETWEEN 1 AND 5",
            name="opportunity_feedback_usefulness",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_opportunity_feedback_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_opportunity_feedback_tenant_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "idempotency_key",
            name="uq_opportunity_feedback_tenant_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_opportunity_feedback_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "opportunity_id"],
            ["opportunities.workspace_id", "opportunities.product_id", "opportunities.id"],
            name="fk_opportunity_feedback_opportunity_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "evaluation_id"],
            [
                "opportunity_evaluations.workspace_id",
                "opportunity_evaluations.product_id",
                "opportunity_evaluations.id",
            ],
            name="fk_opportunity_feedback_evaluation_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_opportunity_feedback_opportunity",
            "workspace_id",
            "product_id",
            "opportunity_id",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    opportunity_id: Mapped[UUID] = mapped_column(nullable=False)
    evaluation_id: Mapped[UUID | None] = mapped_column()
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    usefulness: Mapped[int | None] = mapped_column(Integer)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(500))
    evaluation_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
