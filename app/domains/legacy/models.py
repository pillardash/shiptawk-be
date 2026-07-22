"""Temporary ORM metadata for frontend-owned workflow tables during migration.

These tables preserve the current frontend contract from ``supabase-schema.sql`` except for
restricted GitHub ingress, which is global and ciphertext-only. Tenant tables' ``user_id``
columns are temporary owner-compatibility fields; all authorization must use the new non-null
``workspace_id``. Provider credentials belong only in encrypted ``integration_connections``.
"""

from typing import Any

from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base
from app.domains.products import models as product_models  # noqa: F401

metadata = Base.metadata
json_type = JSONB().with_variant(JSON, "sqlite")
uuid_list_type = ARRAY(UUID(as_uuid=True)).with_variant(JSON, "sqlite")
text_list_type = ARRAY(Text).with_variant(JSON, "sqlite")


def id_column() -> Column[Any]:
    return Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def workspace_column() -> Column[Any]:
    return Column(
        "workspace_id",
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def user_column() -> Column[Any]:
    return Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Temporary legacy owner field; authorize by workspace_id.",
    )


def created_at() -> Column[Any]:
    return Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())


repos = Table(
    "repos",
    metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("tracked_branch", Text),
    Column("repo_name", Text, nullable=False),
    Column("repo_full_name", Text, nullable=False),
    Column("last_checked_at", DateTime(timezone=True)),
    Column("is_tracked", Boolean, nullable=False, server_default="true"),
    created_at(),
    Column("private", Boolean, nullable=False, server_default="false"),
    Column("language", Text),
    Column("description", Text),
    Column("changelog_digest_enabled", Boolean, nullable=False, server_default="false"),
    Column("changelog_digest_frequency", String(20), nullable=False, server_default="weekly"),
    CheckConstraint(
        "changelog_digest_frequency IN ('weekly', 'monthly')",
        name="repos_changelog_digest_frequency",
    ),
    UniqueConstraint("workspace_id", "repo_full_name", name="uq_repos_workspace_full_name"),
)

github_raw_events = Table(
    "github_raw_events",
    metadata,
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
    metadata,
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

normalized_events = Table(
    "normalized_events",
    metadata,
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

event_evaluations = Table(
    "event_evaluations",
    metadata,
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

tweet_candidates = Table(
    "tweet_candidates",
    metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column(
        "normalized_event_id",
        UUID(as_uuid=True),
        ForeignKey("normalized_events.id"),
        nullable=False,
    ),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id"), nullable=False),
    Column("content", Text, nullable=False),
    Column("rank", Integer, nullable=False),
    Column("score", Integer, nullable=False),
    Column("rationale", Text, nullable=False),
    created_at(),
    Column("angle", String(40), nullable=False, server_default="shipping_update"),
    Column("variant", String(40), nullable=False, server_default="concise_shipping_update"),
    Column("dimension_scores", json_type, nullable=False, server_default=text("'{}'")),
    Column("generation_run_id", UUID(as_uuid=True), ForeignKey("generation_runs.id"), index=True),
    CheckConstraint(
        "angle IN ('user_benefit', 'problem_solved', 'shipping_update', "
        "'technical_credibility', 'founder_build_in_public', 'milestone_or_traction', "
        "'weekly_roundup')",
        name="tweet_candidates_angle",
    ),
    CheckConstraint(
        "variant IN ('problem_solution', 'customer_value', 'founder_insight', "
        "'soft_launch_cta', 'concise_shipping_update')",
        name="tweet_candidates_variant",
    ),
)

generation_runs = Table(
    "generation_runs",
    metadata,
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

drafts = Table(
    "drafts",
    metadata,
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
)

draft_status_events = Table(
    "draft_status_events",
    metadata,
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

product_repositories = Table(
    "product_repositories",
    metadata,
    id_column(),
    workspace_column(),
    Column("product_id", UUID(as_uuid=True), ForeignKey("products.id"), nullable=False),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id"), nullable=False, unique=True),
    Column("generated_role_description", Text),
    Column("role_description_override", Text),
    Column("role_description_generated_at", DateTime(timezone=True)),
    Column("role_description_generation_version", Integer),
    Column("repo_role", String(20), nullable=False, server_default="unknown"),
    Column("is_primary_repo", Boolean, nullable=False, server_default="false"),
    created_at(),
    CheckConstraint(
        "repo_role IN ('frontend', 'backend', 'mobile', 'worker', 'infra', 'docs', "
        "'shared_lib', 'unknown')",
        name="product_repositories_repo_role",
    ),
)
Index(
    "uq_product_repositories_primary_product",
    product_repositories.c.workspace_id,
    product_repositories.c.product_id,
    unique=True,
    postgresql_where=product_repositories.c.is_primary_repo.is_(True),
    sqlite_where=product_repositories.c.is_primary_repo.is_(True),
)

achievement_digests = Table(
    "achievement_digests",
    metadata,
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

achievement_digest_feedback = Table(
    "achievement_digest_feedback",
    metadata,
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

draft_feedback_events = Table(
    "draft_feedback_events",
    metadata,
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

repo_changelog_entries = Table(
    "repo_changelog_entries",
    metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id"), nullable=False),
    Column("raw_event_id", UUID(as_uuid=True), ForeignKey("github_raw_events.id"), nullable=False),
    Column("normalized_event_id", UUID(as_uuid=True), ForeignKey("normalized_events.id")),
    Column("sha", Text, nullable=False),
    Column("commit_message", Text, nullable=False),
    Column("commit_url", Text, nullable=False),
    Column("author_name", Text),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("summary", Text, nullable=False),
    Column("user_benefit", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("confidence", Integer, nullable=False),
    Column("provider", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("files_changed", text_list_type, nullable=False, server_default=text("'{}'")),
    Column("is_changelog_worthy", Boolean, nullable=False, server_default="true"),
    Column("safety_flags", text_list_type, nullable=False, server_default=text("'{}'")),
    created_at(),
    UniqueConstraint(
        "workspace_id", "repo_id", "sha", name="uq_repo_changelog_entries_workspace_repo_sha"
    ),
    CheckConstraint("confidence BETWEEN 0 AND 100", name="repo_changelog_entries_confidence"),
)

repo_changelog_digests = Table(
    "repo_changelog_digests",
    metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("frequency", String(20), nullable=False),
    Column("period_start", DateTime(timezone=True), nullable=False),
    Column("period_end", DateTime(timezone=True), nullable=False),
    Column("subject", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("sent_at", DateTime(timezone=True)),
    Column("digest_json", json_type, nullable=False, server_default=text("'{}'")),
    Column("repo_ids", uuid_list_type, nullable=False, server_default=text("'{}'")),
    Column("entry_ids", uuid_list_type, nullable=False, server_default=text("'{}'")),
    created_at(),
    CheckConstraint("frequency IN ('weekly', 'monthly')", name="repo_changelog_digests_frequency"),
)

# Composite tenant keys make it impossible for a child row to point at a parent in another
# workspace even when application authorization is bypassed.
_tenant_parents = (
    repos,
    github_raw_event_consumers,
    normalized_events,
    event_evaluations,
    tweet_candidates,
    generation_runs,
    drafts,
    achievement_digests,
    repo_changelog_entries,
)
for _table in _tenant_parents:
    _table.append_constraint(
        UniqueConstraint("workspace_id", "id", name=f"uq_{_table.name}_workspace_id_id")
    )

_tenant_links = (
    (github_raw_event_consumers, "repo_id", repos),
    (normalized_events, "repo_id", repos),
    (event_evaluations, "normalized_event_id", normalized_events),
    (event_evaluations, "repo_id", repos),
    (tweet_candidates, "normalized_event_id", normalized_events),
    (tweet_candidates, "repo_id", repos),
    (generation_runs, "repo_id", repos),
    (generation_runs, "product_id", metadata.tables["products"]),
    (generation_runs, "normalized_event_id", normalized_events),
    (drafts, "repo_id", repos),
    (drafts, "generation_run_id", generation_runs),
    (drafts, "selected_candidate_id", tweet_candidates),
    (drafts, "normalized_event_id", normalized_events),
    (draft_status_events, "repo_id", repos),
    (draft_status_events, "draft_id", drafts),
    (product_repositories, "product_id", metadata.tables["products"]),
    (product_repositories, "repo_id", repos),
    (achievement_digests, "product_id", metadata.tables["products"]),
    (achievement_digest_feedback, "digest_id", achievement_digests),
    (draft_feedback_events, "product_id", metadata.tables["products"]),
    (draft_feedback_events, "repo_id", repos),
    (draft_feedback_events, "draft_id", drafts),
    (draft_feedback_events, "generation_run_id", generation_runs),
    (draft_feedback_events, "candidate_id", tweet_candidates),
    (draft_feedback_events, "previous_candidate_id", tweet_candidates),
    (repo_changelog_entries, "repo_id", repos),
    (repo_changelog_entries, "normalized_event_id", normalized_events),
)
for _child, _column, _parent in _tenant_links:
    _child.append_constraint(
        ForeignKeyConstraint(
            ["workspace_id", _column],
            [_parent.c.workspace_id, _parent.c.id],
            name=f"fk_{_child.name}_{_column}_tenant",
        )
    )

normalized_events.append_constraint(
    ForeignKeyConstraint(
        ["workspace_id", "raw_event_id"],
        [
            github_raw_event_consumers.c.workspace_id,
            github_raw_event_consumers.c.raw_event_id,
        ],
        name="fk_normalized_events_raw_event_id_consumer",
    )
)
