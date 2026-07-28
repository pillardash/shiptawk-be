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

tweet_candidates = Table(
    "tweet_candidates",
    Base.metadata,
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
        "'technical_credibility', 'founder_build_in_public', "
        "'milestone_or_traction', 'weekly_roundup')",
        name="tweet_candidates_angle",
    ),
    CheckConstraint(
        "variant IN ('problem_solution', 'customer_value', 'founder_insight', "
        "'soft_launch_cta', 'concise_shipping_update')",
        name="tweet_candidates_variant",
    ),
)
add_tenant_parent_key(tweet_candidates)
add_tenant_foreign_key(tweet_candidates, "normalized_event_id", "normalized_events")
add_tenant_foreign_key(tweet_candidates, "repo_id", "repos")
