from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel
from app.modules.products.enums.product_profile_enum import (
    ProductProfileConversionGoal,
    ProductProfileStatus,
)

string_list_type = JSONB().with_variant(JSON(), "sqlite")


class ProductProfile(BaseModel):
    __tablename__ = "product_profiles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_product_profiles_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "product_id", "version", name="uq_product_profiles_version"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_product_profiles_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "based_on_profile_id"],
            ["product_profiles.workspace_id", "product_profiles.id"],
            name="fk_product_profiles_based_on_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('draft', 'approved', 'superseded')",
            name="status_valid",
        ),
        CheckConstraint("draft_revision > 0", name="draft_revision_positive"),
        CheckConstraint("schema_version > 0", name="schema_version_positive"),
        CheckConstraint(
            "completeness_score BETWEEN 0 AND 100",
            name="completeness_score_valid",
        ),
        CheckConstraint(
            "primary_conversion_goal IS NULL OR primary_conversion_goal IN "
            "('awareness', 'waitlist', 'signup', 'trial', 'demo', 'purchase', "
            "'contact', 'newsletter', 'retention')",
            name="conversion_goal_valid",
        ),
        CheckConstraint(
            "(status = 'draft' AND version IS NULL AND approved_at IS NULL "
            "AND approved_by IS NULL) "
            "OR (status IN ('approved', 'superseded') AND version IS NOT NULL "
            "AND approved_at IS NOT NULL AND approved_by IS NOT NULL)",
            name="lifecycle_fields_valid",
        ),
        Index(
            "uq_product_profiles_one_draft",
            "workspace_id",
            "product_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
            sqlite_where=text("status = 'draft'"),
        ),
        Index(
            "uq_product_profiles_one_approved",
            "workspace_id",
            "product_id",
            unique=True,
            postgresql_where=text("status = 'approved'"),
            sqlite_where=text("status = 'approved'"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    status: Mapped[ProductProfileStatus] = mapped_column(
        SqlEnum(ProductProfileStatus, native_enum=False, create_constraint=False, length=16),
        nullable=False,
        default=ProductProfileStatus.draft,
        server_default="draft",
    )
    version: Mapped[int | None] = mapped_column(Integer)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    based_on_profile_id: Mapped[UUID | None] = mapped_column()
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    product_name: Mapped[str | None] = mapped_column(Text)
    short_description: Mapped[str | None] = mapped_column(Text)
    primary_audience: Mapped[str | None] = mapped_column(Text)
    primary_customer_problem: Mapped[str | None] = mapped_column(Text)
    main_value_proposition: Mapped[str | None] = mapped_column(Text)
    primary_conversion_goal: Mapped[ProductProfileConversionGoal | None] = mapped_column(
        SqlEnum(
            ProductProfileConversionGoal,
            native_enum=False,
            create_constraint=False,
            length=32,
        )
    )
    primary_conversion_url: Mapped[str | None] = mapped_column(Text)
    main_market: Mapped[str | None] = mapped_column(Text)
    differentiation: Mapped[str | None] = mapped_column(Text)
    important_capabilities: Mapped[list[str]] = mapped_column(
        string_list_type, nullable=False, default=list, server_default="[]"
    )
    quarterly_objective: Mapped[str | None] = mapped_column(Text)
    restricted_topics: Mapped[list[str]] = mapped_column(
        string_list_type, nullable=False, default=list, server_default="[]"
    )
    restricted_claims: Mapped[list[str]] = mapped_column(
        string_list_type, nullable=False, default=list, server_default="[]"
    )
    restricted_language: Mapped[list[str]] = mapped_column(
        string_list_type, nullable=False, default=list, server_default="[]"
    )
    brand_voice_guidance: Mapped[str | None] = mapped_column(Text)
    completeness_score: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )


product_profiles = ProductProfile.__table__

__all__ = ["ProductProfile", "product_profiles"]
