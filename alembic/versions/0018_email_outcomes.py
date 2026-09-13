"""add durable product email outcomes

Revision ID: 0018_email_outcomes
Revises: 0017_operator_commands
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_email_outcomes"
down_revision: str | None = "0017_operator_commands"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "weekly_growth_deliveries_status",
        "weekly_growth_deliveries",
        "status IN ('pending','delivered','failed','unknown_outcome','suppressed')",
    )
    op.add_column("achievement_digests", sa.Column("delivery_status", sa.String(20)))
    op.add_column("achievement_digests", sa.Column("provider_delivery_id", sa.String(255)))
    op.create_check_constraint(
        "achievement_digests_delivery_status",
        "achievement_digests",
        "delivery_status IS NULL OR delivery_status IN "
        "('pending','delivered','failed','unknown_outcome','suppressed')",
    )


def downgrade() -> None:
    op.drop_constraint("achievement_digests_delivery_status", "achievement_digests", type_="check")
    op.drop_column("achievement_digests", "provider_delivery_id")
    op.drop_column("achievement_digests", "delivery_status")
    op.drop_constraint("weekly_growth_deliveries_status", "weekly_growth_deliveries", type_="check")
