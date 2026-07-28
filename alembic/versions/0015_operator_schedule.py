"""add explicit weekly operator schedule settings

Revision ID: 0015_operator_schedule
Revises: 0014_weekly_operator_foundations
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_operator_schedule"
down_revision: str | None = "0014_weekly_operator_foundations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column(
            "weekly_growth_operator_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("operator_timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("operator_weekday", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operator_hour", sa.Integer(), nullable=False, server_default="9"),
        sa.Column(
            "operator_manual_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "operator_scheduled_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "operator_email_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    for column in columns:
        op.add_column("products", column)
    op.create_check_constraint(
        "products_operator_weekday", "products", "operator_weekday BETWEEN 0 AND 6"
    )
    op.create_check_constraint(
        "products_operator_hour", "products", "operator_hour BETWEEN 0 AND 23"
    )


def downgrade() -> None:
    op.drop_constraint("products_operator_hour", "products", type_="check")
    op.drop_constraint("products_operator_weekday", "products", type_="check")
    for name in (
        "operator_email_enabled",
        "operator_scheduled_enabled",
        "operator_manual_enabled",
        "operator_hour",
        "operator_weekday",
        "operator_timezone",
        "weekly_growth_operator_enabled",
    ):
        op.drop_column("products", name)
