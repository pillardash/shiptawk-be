from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

string_list_type = ARRAY(Text).with_variant(JSON, "sqlite")
json_list_type = JSONB().with_variant(JSON, "sqlite")


class Product(UuidPrimaryKeyMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_products_workspace_id_id"),
        CheckConstraint(
            "tone_override IS NULL OR tone_override IN ('casual', 'technical', 'hype')",
            name="products_tone_override",
        ),
        CheckConstraint("content_mode IN ('builder', 'product')", name="products_content_mode"),
        CheckConstraint(
            "content_goal IN ('', 'awareness', 'waitlist', 'trial', 'retention', "
            "'launch', 'feedback')",
            name="products_content_goal",
        ),
        CheckConstraint("operator_weekday BETWEEN 0 AND 6", name="products_operator_weekday"),
        CheckConstraint("operator_hour BETWEEN 0 AND 23", name="products_operator_hour"),
        CheckConstraint(
            "search_mode IN ('connected', 'deferred_reduced', 'required')",
            name="products_search_mode",
        ),
        CheckConstraint(
            "repository_evidence_mode IN ('undecided', 'connected', 'deferred')",
            name="products_repository_evidence_mode",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Retained because legacy product rows are owner-linked; not part of the public contract.
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    target_audience: Mapped[str | None] = mapped_column(Text)
    messaging_angle: Mapped[str | None] = mapped_column(Text)
    tone_override: Mapped[str | None] = mapped_column(String(20))
    blocked_terms: Mapped[list[str]] = mapped_column(
        json_list_type, nullable=False, default=list, server_default="[]"
    )
    blocked_topics: Mapped[list[str]] = mapped_column(
        json_list_type, nullable=False, default=list, server_default="[]"
    )
    safe_public_boundaries: Mapped[list[str]] = mapped_column(
        json_list_type, nullable=False, default=list, server_default="[]"
    )
    website_url: Mapped[str | None] = mapped_column(Text)
    content_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="builder", server_default="builder"
    )
    primary_customer_pain: Mapped[str | None] = mapped_column(Text)
    desired_outcome: Mapped[str | None] = mapped_column(Text)
    positioning_statement: Mapped[str | None] = mapped_column(Text)
    proof_points: Mapped[list[str]] = mapped_column(
        string_list_type, nullable=False, default=list, server_default=text("'{}'")
    )
    customer_use_cases: Mapped[list[str]] = mapped_column(
        string_list_type, nullable=False, default=list, server_default=text("'{}'")
    )
    content_goal: Mapped[str] = mapped_column(
        String(20), nullable=False, default="", server_default=""
    )
    cta_preference: Mapped[str | None] = mapped_column(Text)
    founder_story_angle: Mapped[str | None] = mapped_column(Text)
    weekly_growth_operator_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    operator_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    operator_weekday: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    operator_hour: Mapped[int] = mapped_column(
        Integer, nullable=False, default=9, server_default="9"
    )
    operator_manual_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    operator_scheduled_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    operator_email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    search_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default="required", server_default="required"
    )
    search_mode_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    repository_evidence_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="undecided", server_default="undecided"
    )
    repository_evidence_mode_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


products = Product.__table__

__all__ = ["Product", "products"]
