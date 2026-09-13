from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin


class PlanView(UuidPrimaryKeyMixin, Base):
    __tablename__ = "plan_views"
    __table_args__ = (
        UniqueConstraint("workspace_id", "product_id", "id", name="uq_plan_views_tenant_id"),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "actor_id",
            "plan_id",
            "plan_revision",
            name="uq_plan_views_actor_plan_revision",
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "actor_id",
            "period_start",
            "period_end",
            name="uq_plan_views_actor_report_period",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "plan_id"],
            ["marketing_plans.workspace_id", "marketing_plans.product_id", "marketing_plans.id"],
            ondelete="CASCADE",
            name="fk_plan_views_plan_tenant",
        ),
        Index(
            "ix_plan_views_actor_product_ordinal",
            "workspace_id",
            "product_id",
            "actor_id",
            "report_ordinal",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(nullable=False)
    plan_revision: Mapped[int] = mapped_column(nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    report_ordinal: Mapped[int] = mapped_column(nullable=False)
    second_report_return: Mapped[bool] = mapped_column(nullable=False, default=False)
    fourth_report_return: Mapped[bool] = mapped_column(nullable=False, default=False)
    valid_no_recommendation_report: Mapped[bool] = mapped_column(nullable=False, default=False)
    first_viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
