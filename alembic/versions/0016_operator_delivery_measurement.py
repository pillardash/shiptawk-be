"""add operator delivery, measurement, and X publishing bridges

Revision ID: 0016_operator_delivery
Revises: 0015_operator_schedule
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_operator_delivery"
down_revision: str | None = "0015_operator_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_prepared_assets_workspace_id_revision",
        "prepared_assets",
        ["workspace_id", "id", "revision"],
    )
    op.add_column("drafts", sa.Column("prepared_asset_id", sa.Uuid()))
    op.add_column("drafts", sa.Column("prepared_asset_revision", sa.Integer()))
    op.create_unique_constraint(
        "uq_drafts_prepared_asset_revision",
        "drafts",
        ["workspace_id", "prepared_asset_id", "prepared_asset_revision"],
    )
    op.create_foreign_key(
        "fk_drafts_prepared_asset_revision",
        "drafts",
        "prepared_assets",
        ["workspace_id", "prepared_asset_id", "prepared_asset_revision"],
        ["workspace_id", "id", "revision"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "drafts_prepared_asset_pair",
        "drafts",
        "(prepared_asset_id IS NULL AND prepared_asset_revision IS NULL) OR "
        "(prepared_asset_id IS NOT NULL AND prepared_asset_revision > 0)",
    )
    op.create_table(
        "publication_attempts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="x"),
        sa.Column("provider_receipt", JSONB),
        sa.Column("error_category", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_publication_attempts"),
        sa.UniqueConstraint("workspace_id", "command_id", name="uq_publication_attempts_command"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_publication_attempts_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "draft_id"],
            ["drafts.workspace_id", "drafts.id"],
            name="fk_publication_attempts_draft_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('pending','completed','failed','unknown_outcome')",
            name="publication_attempts_status",
        ),
    )
    op.create_index(
        "ix_publication_attempts_workspace_id", "publication_attempts", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_table("publication_attempts")
    op.drop_constraint("drafts_prepared_asset_pair", "drafts", type_="check")
    op.drop_constraint("fk_drafts_prepared_asset_revision", "drafts", type_="foreignkey")
    op.drop_constraint("uq_drafts_prepared_asset_revision", "drafts", type_="unique")
    op.drop_column("drafts", "prepared_asset_revision")
    op.drop_column("drafts", "prepared_asset_id")
    op.drop_constraint(
        "uq_prepared_assets_workspace_id_revision", "prepared_assets", type_="unique"
    )
