"""add operator command receipts and decision audit history

Revision ID: 0017_operator_commands
Revises: 0016_operator_delivery
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_operator_commands"
down_revision: str | None = "0016_operator_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plan_actions", sa.Column("dismissal_comment", sa.Text()))
    op.create_table(
        "operator_recommendation_decisions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64)),
        sa.Column("comment", sa.Text()),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operator_recommendation_decisions"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "recommendation_id"],
            [
                "operator_recommendations.workspace_id",
                "operator_recommendations.product_id",
                "operator_recommendations.id",
            ],
            name="fk_operator_recommendation_decisions_recommendation_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_operator_recommendation_decisions_workspace_id",
        "operator_recommendation_decisions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_operator_recommendation_decisions_recommendation_id",
        "operator_recommendation_decisions",
        ["recommendation_id"],
    )
    op.create_table(
        "operator_command_receipts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("result_resource_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operator_command_receipts"),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "operation",
            "resource_id",
            "idempotency_key",
            name="uq_operator_command_receipts_command",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_operator_command_receipts_product_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_operator_command_receipts_workspace_id", "operator_command_receipts", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_table("operator_command_receipts")
    op.drop_table("operator_recommendation_decisions")
    op.drop_column("plan_actions", "dismissal_comment")
