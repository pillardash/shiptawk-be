"""add integration OAuth audit indexes

Revision ID: 0005_oauth_audit_indexes
Revises: 0004_workspace_onboarding
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_oauth_audit_indexes"
down_revision: str | None = "0004_workspace_onboarding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("created_by", "updated_by", "deleted_at"):
        op.create_index(
            op.f(f"ix_integration_oauth_transactions_{column}"),
            "integration_oauth_transactions",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in reversed(("created_by", "updated_by", "deleted_at")):
        op.drop_index(
            op.f(f"ix_integration_oauth_transactions_{column}"),
            table_name="integration_oauth_transactions",
        )
