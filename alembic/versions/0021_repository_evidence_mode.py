"""add explicit repository evidence decision

Revision ID: 0021_repository_evidence
Revises: 0020_activation_journey
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_repository_evidence"
down_revision: str | None = "0020_activation_journey"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "repository_evidence_mode",
            sa.String(16),
            nullable=False,
            server_default="deferred",
        ),
    )
    op.add_column(
        "products",
        sa.Column("repository_evidence_mode_decided_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "products_repository_evidence_mode",
        "products",
        "repository_evidence_mode IN ('connected', 'deferred')",
    )
    op.execute(
        "UPDATE products SET repository_evidence_mode = 'connected', "
        "repository_evidence_mode_decided_at = now() WHERE EXISTS ("
        "SELECT 1 FROM product_repositories pr JOIN repos r "
        "ON r.workspace_id = pr.workspace_id AND r.id = pr.repo_id "
        "WHERE pr.workspace_id = products.workspace_id AND pr.product_id = products.id "
        "AND r.is_tracked = true)"
    )
    op.create_index(
        "ix_products_workspace_repository_evidence_mode",
        "products",
        ["workspace_id", "repository_evidence_mode"],
    )


def downgrade() -> None:
    op.drop_index("ix_products_workspace_repository_evidence_mode", table_name="products")
    op.drop_constraint("products_repository_evidence_mode", "products", type_="check")
    op.drop_column("products", "repository_evidence_mode_decided_at")
    op.drop_column("products", "repository_evidence_mode")
