"""create product tenancy foundation

Revision ID: 20260719_0003
Revises: 20260531_0002
Create Date: 2026-07-19

Fresh/local databases only. See docs/product-tenancy-reconciliation.md before adapting this
migration to an existing production schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260719_0003"
down_revision: str | None = "20260531_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )
    op.create_index(op.f("ix_workspaces_created_by"), "workspaces", ["created_by"])
    op.create_index(op.f("ix_workspaces_deleted_at"), "workspaces", ["deleted_at"])
    op.create_index(op.f("ix_workspaces_updated_by"), "workspaces", ["updated_by"])
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_workspace_memberships_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name=op.f("fk_workspace_memberships_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", name=op.f("pk_workspace_memberships")),
        sa.UniqueConstraint(
            "workspace_id", "user_id", name="uq_workspace_memberships_workspace_user"
        ),
    )
    op.create_table(
        "products",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_audience", sa.Text(), nullable=True),
        sa.Column("messaging_angle", sa.Text(), nullable=True),
        sa.Column("tone_override", sa.String(length=20), nullable=True),
        sa.Column(
            "blocked_terms",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "blocked_topics",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "safe_public_boundaries",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("primary_customer_pain", sa.Text(), nullable=True),
        sa.Column("desired_outcome", sa.Text(), nullable=True),
        sa.Column("positioning_statement", sa.Text(), nullable=True),
        sa.Column(
            "proof_points",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "customer_use_cases",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("content_goal", sa.String(length=20), server_default="", nullable=False),
        sa.Column("cta_preference", sa.Text(), nullable=True),
        sa.Column("founder_story_angle", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "content_goal IN ('', 'awareness', 'waitlist', 'trial', 'retention', "
            "'launch', 'feedback')",
            name=op.f("ck_products_products_content_goal"),
        ),
        sa.CheckConstraint(
            "tone_override IS NULL OR tone_override IN ('casual', 'technical', 'hype')",
            name=op.f("ck_products_products_tone_override"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name=op.f("fk_products_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name=op.f("fk_products_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )
    op.create_index(op.f("ix_products_user_id"), "products", ["user_id"])
    op.create_index(op.f("ix_products_workspace_id"), "products", ["workspace_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_products_workspace_id"), table_name="products")
    op.drop_index(op.f("ix_products_user_id"), table_name="products")
    op.drop_table("products")
    op.drop_table("workspace_memberships")
    op.drop_index(op.f("ix_workspaces_updated_by"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_deleted_at"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_created_by"), table_name="workspaces")
    op.drop_table("workspaces")
