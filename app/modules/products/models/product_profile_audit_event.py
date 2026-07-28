from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel
from app.modules.products.enums.product_profile_enum import ProductProfileAuditAction
from app.modules.products.models.product_profile import string_list_type


class ProductProfileAuditEvent(BaseModel):
    __tablename__ = "product_profile_audit_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('draft_created', 'draft_updated', 'profile_approved', "
            "'profile_superseded')",
            name="action_valid",
        ),
        CheckConstraint("draft_revision > 0", name="draft_revision_positive"),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_product_profile_audit_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "profile_id"],
            ["product_profiles.workspace_id", "product_profiles.id"],
            name="fk_product_profile_audit_profile_tenant",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    profile_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    action: Mapped[ProductProfileAuditAction] = mapped_column(
        SqlEnum(ProductProfileAuditAction, native_enum=False, create_constraint=False, length=32),
        nullable=False,
    )
    profile_version: Mapped[int | None] = mapped_column(Integer)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_fields: Mapped[list[str]] = mapped_column(
        string_list_type, nullable=False, default=list, server_default="[]"
    )


product_profile_audit_events = ProductProfileAuditEvent.__table__

__all__ = ["ProductProfileAuditEvent", "product_profile_audit_events"]
