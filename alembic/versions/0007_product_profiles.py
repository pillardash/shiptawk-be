"""add versioned product profiles

Revision ID: 0007_product_profiles
Revises: 0006_transactional_outbox
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_product_profiles"
down_revision: str | None = "0006_transactional_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_profiles",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("draft_revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("based_on_profile_id", sa.Uuid(), nullable=True),
        sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("short_description", sa.Text(), nullable=True),
        sa.Column("primary_audience", sa.Text(), nullable=True),
        sa.Column("primary_customer_problem", sa.Text(), nullable=True),
        sa.Column("main_value_proposition", sa.Text(), nullable=True),
        sa.Column("primary_conversion_goal", sa.String(32), nullable=True),
        sa.Column("primary_conversion_url", sa.Text(), nullable=True),
        sa.Column("main_market", sa.Text(), nullable=True),
        sa.Column("differentiation", sa.Text(), nullable=True),
        sa.Column(
            "important_capabilities",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("quarterly_objective", sa.Text(), nullable=True),
        sa.Column(
            "restricted_topics",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "restricted_claims",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "restricted_language",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("brand_voice_guidance", sa.Text(), nullable=True),
        sa.Column("completeness_score", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'approved', 'superseded')", name="status_valid"),
        sa.CheckConstraint("draft_revision > 0", name="draft_revision_positive"),
        sa.CheckConstraint("schema_version > 0", name="schema_version_positive"),
        sa.CheckConstraint("completeness_score BETWEEN 0 AND 100", name="completeness_score_valid"),
        sa.CheckConstraint(
            "primary_conversion_goal IS NULL OR primary_conversion_goal IN "
            "('awareness', 'waitlist', 'signup', 'trial', 'demo', 'purchase', "
            "'contact', 'newsletter', 'retention')",
            name="conversion_goal_valid",
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND version IS NULL AND approved_at IS NULL "
            "AND approved_by IS NULL) "
            "OR (status IN ('approved', 'superseded') AND version IS NOT NULL "
            "AND approved_at IS NOT NULL AND approved_by IS NOT NULL)",
            name="lifecycle_fields_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_profiles"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_product_profiles_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "version", name="uq_product_profiles_version"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_product_profiles_product_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "based_on_profile_id"],
            ["product_profiles.workspace_id", "product_profiles.id"],
            name="fk_product_profiles_based_on_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["users.id"],
            name="fk_product_profiles_approved_by_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_product_profiles_workspace_id", "product_profiles", ["workspace_id"])
    op.create_index("ix_product_profiles_product_id", "product_profiles", ["product_id"])
    op.create_index("ix_product_profiles_approved_by", "product_profiles", ["approved_by"])
    op.create_index("ix_product_profiles_created_by", "product_profiles", ["created_by"])
    op.create_index("ix_product_profiles_updated_by", "product_profiles", ["updated_by"])
    op.create_index("ix_product_profiles_deleted_at", "product_profiles", ["deleted_at"])
    op.create_index(
        "uq_product_profiles_one_draft",
        "product_profiles",
        ["workspace_id", "product_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.create_index(
        "uq_product_profiles_one_approved",
        "product_profiles",
        ["workspace_id", "product_id"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )

    op.create_table(
        "product_profile_audit_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=True),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column(
            "changed_fields",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action IN ('draft_created', 'draft_updated', 'profile_approved', "
            "'profile_superseded')",
            name="action_valid",
        ),
        sa.CheckConstraint("draft_revision > 0", name="draft_revision_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_product_profile_audit_events"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_product_profile_audit_product_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "profile_id"],
            ["product_profiles.workspace_id", "product_profiles.id"],
            name="fk_product_profile_audit_profile_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_product_profile_audit_events_actor_id_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_product_profile_audit_events_workspace_id",
        "product_profile_audit_events",
        ["workspace_id"],
    )
    op.create_index(
        "ix_product_profile_audit_events_product_id", "product_profile_audit_events", ["product_id"]
    )
    op.create_index(
        "ix_product_profile_audit_events_profile_id", "product_profile_audit_events", ["profile_id"]
    )
    op.create_index(
        "ix_product_profile_audit_events_actor_id", "product_profile_audit_events", ["actor_id"]
    )
    op.create_index(
        "ix_product_profile_audit_events_created_by",
        "product_profile_audit_events",
        ["created_by"],
    )
    op.create_index(
        "ix_product_profile_audit_events_updated_by",
        "product_profile_audit_events",
        ["updated_by"],
    )
    op.create_index(
        "ix_product_profile_audit_events_deleted_at",
        "product_profile_audit_events",
        ["deleted_at"],
    )


def downgrade() -> None:
    op.drop_table("product_profile_audit_events")
    op.drop_index("uq_product_profiles_one_approved", table_name="product_profiles")
    op.drop_index("uq_product_profiles_one_draft", table_name="product_profiles")
    op.drop_table("product_profiles")
