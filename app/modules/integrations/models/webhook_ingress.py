from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
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

github_raw_events = Table(
    "github_raw_events",
    Base.metadata,
    id_column(),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("github_delivery_id", Text, nullable=False),
    Column("event_name", Text, nullable=False),
    Column("repo_full_name", Text, nullable=False),
    Column("payload_ciphertext", Text, nullable=False),
    Column("payload_key_version", String(64), nullable=False),
    UniqueConstraint("github_delivery_id", name="uq_github_raw_events_delivery"),
    comment="Restricted provider ingress; never expose through product APIs or logs.",
)

github_raw_event_consumers = Table(
    "github_raw_event_consumers",
    Base.metadata,
    id_column(),
    workspace_column(),
    Column(
        "raw_event_id",
        UUID(as_uuid=True),
        ForeignKey("github_raw_events.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id"), nullable=False),
    user_column(),
    Column("processing_state", String(20), nullable=False, server_default="pending"),
    Column("publish_attempts", Integer, nullable=False, server_default="0"),
    Column("publish_error_category", String(32)),
    Column("processing_started_at", DateTime(timezone=True)),
    Column("processed_at", DateTime(timezone=True)),
    created_at(),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "processing_state IN ('pending', 'enqueued', 'processed', 'failed')",
        name="github_raw_event_consumers_processing_state",
    ),
    CheckConstraint("publish_attempts >= 0", name="github_raw_event_consumers_publish_attempts"),
    UniqueConstraint(
        "workspace_id", "raw_event_id", name="uq_github_raw_event_consumers_workspace_raw_event"
    ),
)
add_tenant_parent_key(github_raw_event_consumers)
add_tenant_foreign_key(github_raw_event_consumers, "repo_id", "repos")

normalized_events = Table(
    "normalized_events",
    Base.metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("raw_event_id", UUID(as_uuid=True), ForeignKey("github_raw_events.id"), nullable=False),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id"), nullable=False),
    Column("event_type", Text, nullable=False),
    Column("repo_full_name", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("sha", Text),
    Column("dedupe_key", Text, nullable=False),
    Column("enrichment", json_type),
    Column("branch_name", Text),
    Column("touched_paths", json_type, nullable=False, server_default=text("'[]'")),
    created_at(),
    UniqueConstraint("workspace_id", "dedupe_key", name="uq_normalized_events_workspace_dedupe"),
)
add_tenant_parent_key(normalized_events)
add_tenant_foreign_key(normalized_events, "repo_id", "repos")
normalized_events.append_constraint(
    ForeignKeyConstraint(
        ["workspace_id", "raw_event_id"],
        [
            "github_raw_event_consumers.workspace_id",
            "github_raw_event_consumers.raw_event_id",
        ],
        name="fk_normalized_events_raw_event_id_consumer",
    )
)
