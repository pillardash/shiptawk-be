"""add generic integration OAuth and credential foundations

Revision ID: 0009_generic_integrations
Revises: 0008_website_intelligence
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_generic_integrations"
down_revision: str | None = "0008_website_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "integration_oauth_transactions", sa.Column("purpose", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "integration_oauth_transactions",
        sa.Column("subject_type", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "integration_oauth_transactions", sa.Column("subject_id", sa.Uuid(), nullable=True)
    )
    op.create_index(
        "ix_integration_oauth_transactions_subject_id",
        "integration_oauth_transactions",
        ["subject_id"],
    )

    op.alter_column("integration_connections", "credentials_ciphertext", nullable=True)
    op.alter_column("integration_connections", "credential_key_version", nullable=True)
    op.add_column(
        "integration_connections",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_integration_connections_revoked_at", "integration_connections", ["revoked_at"]
    )
    op.create_unique_constraint(
        "uq_integration_connections_workspace_id_id",
        "integration_connections",
        ["workspace_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_integration_connections_workspace_id_id",
        "integration_connections",
        type_="unique",
    )
    op.drop_index("ix_integration_connections_revoked_at", table_name="integration_connections")
    op.execute(
        sa.text(
            "UPDATE integration_connections "
            "SET credentials_ciphertext = 'revoked', credential_key_version = 'none' "
            "WHERE credentials_ciphertext IS NULL OR credential_key_version IS NULL"
        )
    )
    op.drop_column("integration_connections", "revoked_at")
    op.alter_column("integration_connections", "credential_key_version", nullable=False)
    op.alter_column("integration_connections", "credentials_ciphertext", nullable=False)

    op.drop_index(
        "ix_integration_oauth_transactions_subject_id",
        table_name="integration_oauth_transactions",
    )
    op.drop_column("integration_oauth_transactions", "subject_id")
    op.drop_column("integration_oauth_transactions", "subject_type")
    op.drop_column("integration_oauth_transactions", "purpose")
