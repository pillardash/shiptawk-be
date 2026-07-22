"""Support product context and versioned draft candidate generation.

Revision ID: 0003_generation_use_cases
Revises: 0002_integration_oauth_transactions
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_generation_use_cases"
down_revision: str | None = "0002_integration_oauth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("generation_runs", "repo_id", existing_type=sa.Uuid(), nullable=True)
    op.drop_constraint("generation_runs_trigger", "generation_runs", type_="check")
    op.create_check_constraint(
        "generation_runs_trigger",
        "generation_runs",
        "trigger IN ('event_pipeline', 'angle_regeneration', 'product_context')",
    )
    op.add_column("tweet_candidates", sa.Column("generation_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_tweet_candidates_generation_run_id_generation_runs",
        "tweet_candidates",
        "generation_runs",
        ["generation_run_id"],
        ["id"],
    )
    op.create_index(
        "ix_tweet_candidates_generation_run_id", "tweet_candidates", ["generation_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tweet_candidates_generation_run_id", table_name="tweet_candidates")
    op.drop_constraint(
        "fk_tweet_candidates_generation_run_id_generation_runs",
        "tweet_candidates",
        type_="foreignkey",
    )
    op.drop_column("tweet_candidates", "generation_run_id")
    op.drop_constraint("generation_runs_trigger", "generation_runs", type_="check")
    op.create_check_constraint(
        "generation_runs_trigger",
        "generation_runs",
        "trigger IN ('event_pipeline', 'angle_regeneration')",
    )
    op.alter_column("generation_runs", "repo_id", existing_type=sa.Uuid(), nullable=False)
