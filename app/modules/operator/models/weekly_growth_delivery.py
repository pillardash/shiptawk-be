from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


class WeeklyGrowthDelivery(UuidPrimaryKeyMixin, Base):
    __tablename__ = "weekly_growth_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_weekly_growth_deliveries_tenant_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "idempotency_key",
            name="uq_weekly_growth_deliveries_delivery_key",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "plan_id"],
            ["marketing_plans.workspace_id", "marketing_plans.product_id", "marketing_plans.id"],
            name="fk_weekly_growth_deliveries_plan_tenant",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    plan_id: Mapped[UUID] = mapped_column(nullable=False)
    plan_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_delivery_id: Mapped[str | None] = mapped_column(String(255))
    provider_receipt: Mapped[dict[str, object] | None] = mapped_column(json_type)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
