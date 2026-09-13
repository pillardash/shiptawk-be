from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from app.db.base import Base
from app.db.core_tables import (
    add_tenant_parent_key,
    created_at,
    id_column,
    user_column,
    workspace_column,
)

repos = Table(
    "repos",
    Base.metadata,
    id_column(),
    workspace_column(),
    user_column(),
    Column("tracked_branch", Text),
    Column("repo_name", Text, nullable=False),
    Column("repo_full_name", Text, nullable=False),
    Column("last_checked_at", DateTime(timezone=True)),
    Column("is_tracked", Boolean, nullable=False, server_default="true"),
    Column("tracking_generation", Integer, nullable=False, server_default="0"),
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
add_tenant_parent_key(repos)
