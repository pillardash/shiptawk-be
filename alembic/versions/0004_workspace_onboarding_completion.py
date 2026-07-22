"""Scope onboarding completion to workspaces.

Revision ID: 0004_workspace_onboarding_completion
Revises: 0003_generation_use_cases
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_workspace_onboarding_completion"
down_revision: str | None = "0003_generation_use_cases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "onboarding_completed")
