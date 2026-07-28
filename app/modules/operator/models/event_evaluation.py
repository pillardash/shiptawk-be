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
    func,
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
    workspace_column,
)

event_evaluations = Table(
    "event_evaluations",
    Base.metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column(
        "normalized_event_id",
        UUID(as_uuid=True),
        ForeignKey("normalized_events.id"),
        nullable=False,
        unique=True,
    ),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id"), nullable=False),
    Column("public_worthiness_score", Integer, nullable=False),
    Column("is_public_worthy", Boolean, nullable=False),
    Column("rationale", Text, nullable=False),
    Column("failed_safety_checks", json_type, nullable=False, server_default=text("'[]'")),
    created_at(),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("outcome", String(20), nullable=False, server_default="ignore"),
    Column("dimension_scores", json_type, nullable=False, server_default=text("'{}'")),
    Column("internal_summary", Text),
    Column("public_summary", Text),
    Column("user_benefit", Text),
    Column("target_audience", Text),
    Column("confidence", Integer),
    CheckConstraint(
        "outcome IN ('generate', 'hold', 'ignore', 'block')", name="event_evaluations_outcome"
    ),
)
add_tenant_parent_key(event_evaluations)
add_tenant_foreign_key(event_evaluations, "normalized_event_id", "normalized_events")
add_tenant_foreign_key(event_evaluations, "repo_id", "repos")
