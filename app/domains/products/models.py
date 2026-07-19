from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

string_list_type = ARRAY(Text).with_variant(JSON, "sqlite")
json_list_type = JSONB().with_variant(JSON, "sqlite")


class Product(UuidPrimaryKeyMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "tone_override IS NULL OR tone_override IN ('casual', 'technical', 'hype')",
            name="products_tone_override",
        ),
        CheckConstraint(
            "content_goal IN ('', 'awareness', 'waitlist', 'trial', 'retention', "
            "'launch', 'feedback')",
            name="products_content_goal",
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
    primary_customer_pain: Mapped[str | None] = mapped_column(Text)
    desired_outcome: Mapped[str | None] = mapped_column(Text)
    positioning_statement: Mapped[str | None] = mapped_column(Text)
    proof_points: Mapped[list[str]] = mapped_column(string_list_type, nullable=False, default=list)
    customer_use_cases: Mapped[list[str]] = mapped_column(
        string_list_type, nullable=False, default=list
    )
    content_goal: Mapped[str] = mapped_column(
        String(20), nullable=False, default="", server_default=""
    )
    cta_preference: Mapped[str | None] = mapped_column(Text)
    founder_story_angle: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
