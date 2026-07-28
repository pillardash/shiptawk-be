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
    UniqueConstraint,
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
    text_list_type,
    user_column,
    uuid_list_type,
    workspace_column,
)

repo_changelog_entries = Table(
    "repo_changelog_entries",
    Base.metadata,
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
add_tenant_parent_key(repo_changelog_entries)
add_tenant_foreign_key(repo_changelog_entries, "repo_id", "repos")
add_tenant_foreign_key(repo_changelog_entries, "normalized_event_id", "normalized_events")

repo_changelog_digests = Table(
    "repo_changelog_digests",
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
    Column("digest_json", json_type, nullable=False, server_default=text("'{}'")),
    Column("repo_ids", uuid_list_type, nullable=False, server_default=text("'{}'")),
    Column("entry_ids", uuid_list_type, nullable=False, server_default=text("'{}'")),
    created_at(),
    CheckConstraint("frequency IN ('weekly', 'monthly')", name="repo_changelog_digests_frequency"),
)
