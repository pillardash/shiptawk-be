from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.db.core_tables import (
    add_tenant_foreign_key,
    add_tenant_parent_key,
    created_at,
    id_column,
    json_type,
    user_column,
    uuid_list_type,
    workspace_column,
)

achievement_digests = Table(
    "achievement_digests",
    Base.metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("frequency", String(20), nullable=False),
    Column("period_start", DateTime(timezone=True), nullable=False),
    Column("period_end", DateTime(timezone=True), nullable=False),
    Column("subject", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("sent_at", DateTime(timezone=True)),
    created_at(),
    Column("product_id", UUID(as_uuid=True), ForeignKey("products.id")),
    Column("digest_json", json_type, nullable=False, server_default=text("'{}'")),
    Column("quality_score", Integer),
    Column("should_send", Boolean, nullable=False, server_default="true"),
    Column("recommended_post", Text),
    Column("event_ids", uuid_list_type, nullable=False, server_default=text("'{}'")),
    Column("draft_ids", uuid_list_type, nullable=False, server_default=text("'{}'")),
    Column("repo_ids", uuid_list_type, nullable=False, server_default=text("'{}'")),
    CheckConstraint("frequency IN ('weekly', 'monthly')", name="achievement_digests_frequency"),
    CheckConstraint(
        "quality_score IS NULL OR quality_score BETWEEN 0 AND 100",
        name="achievement_digests_quality_score",
    ),
)
add_tenant_parent_key(achievement_digests)
add_tenant_foreign_key(achievement_digests, "product_id", "products")

achievement_digest_feedback = Table(
    "achievement_digest_feedback",
    Base.metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("digest_id", UUID(as_uuid=True), ForeignKey("achievement_digests.id"), nullable=False),
    Column("action", String(30), nullable=False),
    Column("metadata", json_type, nullable=False, server_default=text("'{}'")),
    created_at(),
    CheckConstraint(
        "action IN ('useful', 'not_useful', 'copied', 'converted_to_draft', 'dismissed')",
        name="achievement_digest_feedback_action",
    ),
)
add_tenant_foreign_key(achievement_digest_feedback, "digest_id", "achievement_digests")
