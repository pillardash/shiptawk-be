from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Table, Text, text
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

generation_runs = Table(
    "generation_runs",
    Base.metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id")),
    Column("product_id", UUID(as_uuid=True), ForeignKey("products.id")),
    Column("normalized_event_id", UUID(as_uuid=True), ForeignKey("normalized_events.id")),
    Column("trigger", String(30), nullable=False),
    Column("requested_angle", Text),
    Column("context_version", Integer, nullable=False),
    Column("context_snapshot", json_type, nullable=False),
    Column("provider", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("system_prompt", Text, nullable=False),
    Column("user_prompt", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("failure_message", Text),
    Column("duration_ms", Integer),
    Column(
        "content_memory_snapshot",
        json_type,
        nullable=False,
        server_default=text('\'{"approvedAngles": [], "recentApprovedContent": []}\''),
    ),
    Column("candidate_snapshot", json_type, nullable=False, server_default=text("'[]'")),
    Column("provider_parameters", json_type, nullable=False, server_default=text("'{}'")),
    created_at(),
    CheckConstraint(
        "trigger IN ('event_pipeline', 'angle_regeneration', 'product_context')",
        name="generation_runs_trigger",
    ),
    CheckConstraint("status IN ('completed', 'failed')", name="generation_runs_status"),
    CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="generation_runs_duration"),
)
add_tenant_parent_key(generation_runs)
add_tenant_foreign_key(generation_runs, "repo_id", "repos")
add_tenant_foreign_key(generation_runs, "product_id", "products")
add_tenant_foreign_key(generation_runs, "normalized_event_id", "normalized_events")
