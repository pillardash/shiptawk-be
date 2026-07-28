from sqlalchemy import (
    Boolean,
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

drafts = Table(
    "drafts",
    Base.metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id"), nullable=False),
    Column("content", Text, nullable=False),
    Column("source_event_type", Text, nullable=False),
    Column("posted_at", DateTime(timezone=True)),
    Column("tweet_id", Text),
    Column("source_event_payload", json_type, nullable=False, server_default=text("'{}'")),
    Column("status", String(20), nullable=False, server_default="pending"),
    created_at(),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("user_edited", Boolean, nullable=False, server_default="false"),
    Column("edit_distance", Integer),
    Column("generation_run_id", UUID(as_uuid=True), ForeignKey("generation_runs.id")),
    Column("selected_candidate_id", UUID(as_uuid=True), ForeignKey("tweet_candidates.id")),
    Column("normalized_event_id", UUID(as_uuid=True), ForeignKey("normalized_events.id")),
    Column("approved_at", DateTime(timezone=True)),
    Column("rejected_at", DateTime(timezone=True)),
    Column("rejection_reason", Text),
    Column("posting_started_at", DateTime(timezone=True)),
    Column("prepared_asset_id", UUID(as_uuid=True)),
    Column("prepared_asset_revision", Integer),
    CheckConstraint(
        "status IN ('pending', 'approved', 'rejected', 'posted')", name="drafts_status"
    ),
    CheckConstraint(
        "status NOT IN ('approved', 'posted') OR approved_at IS NOT NULL",
        name="drafts_approval_required",
    ),
    CheckConstraint(
        "status <> 'posted' OR (posted_at IS NOT NULL AND tweet_id IS NOT NULL)",
        name="drafts_post_receipt_required",
    ),
    CheckConstraint(
        "(prepared_asset_id IS NULL AND prepared_asset_revision IS NULL) OR "
        "(prepared_asset_id IS NOT NULL AND prepared_asset_revision > 0)",
        name="drafts_prepared_asset_pair",
    ),
    UniqueConstraint(
        "workspace_id",
        "prepared_asset_id",
        "prepared_asset_revision",
        name="uq_drafts_prepared_asset_revision",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "prepared_asset_id", "prepared_asset_revision"],
        ["prepared_assets.workspace_id", "prepared_assets.id", "prepared_assets.revision"],
        name="fk_drafts_prepared_asset_revision",
        ondelete="RESTRICT",
    ),
)
add_tenant_parent_key(drafts)
add_tenant_foreign_key(drafts, "repo_id", "repos")
add_tenant_foreign_key(drafts, "generation_run_id", "generation_runs")
add_tenant_foreign_key(drafts, "selected_candidate_id", "tweet_candidates")
add_tenant_foreign_key(drafts, "normalized_event_id", "normalized_events")

publication_attempts = Table(
    "publication_attempts",
    Base.metadata,
    id_column(),
    workspace_column(),
    Column("draft_id", UUID(as_uuid=True), nullable=False),
    Column("command_id", String(255), nullable=False),
    Column("status", String(24), nullable=False),
    Column("provider", String(32), nullable=False, server_default="x"),
    Column("provider_receipt", json_type),
    Column("error_category", String(64)),
    created_at(),
    Column("completed_at", DateTime(timezone=True)),
    CheckConstraint(
        "status IN ('pending', 'completed', 'failed', 'unknown_outcome')",
        name="publication_attempts_status",
    ),
    UniqueConstraint("workspace_id", "command_id", name="uq_publication_attempts_command"),
    ForeignKeyConstraint(
        ["workspace_id", "draft_id"],
        ["drafts.workspace_id", "drafts.id"],
        name="fk_publication_attempts_draft_tenant",
        ondelete="RESTRICT",
    ),
)

draft_status_events = Table(
    "draft_status_events",
    Base.metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id"), nullable=False),
    Column("draft_id", UUID(as_uuid=True), ForeignKey("drafts.id"), nullable=False),
    Column("status", String(20), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    created_at(),
    CheckConstraint(
        "status IN ('pending', 'approved', 'rejected', 'posted')", name="draft_status_events_status"
    ),
)
add_tenant_foreign_key(draft_status_events, "repo_id", "repos")
add_tenant_foreign_key(draft_status_events, "draft_id", "drafts")

draft_feedback_events = Table(
    "draft_feedback_events",
    Base.metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("product_id", UUID(as_uuid=True), ForeignKey("products.id")),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id"), nullable=False),
    Column("draft_id", UUID(as_uuid=True), ForeignKey("drafts.id"), nullable=False),
    Column("generation_run_id", UUID(as_uuid=True), ForeignKey("generation_runs.id")),
    Column("candidate_id", UUID(as_uuid=True), ForeignKey("tweet_candidates.id")),
    Column("event_type", String(20), nullable=False),
    Column("previous_candidate_id", UUID(as_uuid=True), ForeignKey("tweet_candidates.id")),
    Column("content_before", Text),
    Column("content_after", Text),
    Column("edit_distance", Integer),
    Column("rejection_reason", Text),
    Column("provider_post_id", Text),
    Column("metadata", json_type, nullable=False, server_default=text("'{}'")),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    created_at(),
    CheckConstraint(
        "event_type IN ('selected', 'edited', 'approved', 'rejected', 'posted')",
        name="draft_feedback_events_event_type",
    ),
    CheckConstraint(
        "edit_distance IS NULL OR edit_distance >= 0", name="draft_feedback_events_edit_distance"
    ),
)
add_tenant_foreign_key(draft_feedback_events, "product_id", "products")
add_tenant_foreign_key(draft_feedback_events, "repo_id", "repos")
add_tenant_foreign_key(draft_feedback_events, "draft_id", "drafts")
add_tenant_foreign_key(draft_feedback_events, "generation_run_id", "generation_runs")
add_tenant_foreign_key(draft_feedback_events, "candidate_id", "tweet_candidates")
add_tenant_foreign_key(draft_feedback_events, "previous_candidate_id", "tweet_candidates")
