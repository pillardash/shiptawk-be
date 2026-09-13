"""add authoritative activation and logical report periods

Revision ID: 0020_activation_journey
Revises: 0019_private_beta
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_activation_journey"
down_revision: str | None = "0019_private_beta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("activation_product_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_workspaces_activation_product",
        "workspaces",
        "products",
        ["activation_product_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        "UPDATE workspaces SET activation_product_id = candidate.product_id FROM "
        "(SELECT workspace_id, id AS product_id FROM products WHERE workspace_id IN "
        "(SELECT workspace_id FROM products GROUP BY workspace_id HAVING count(*) = 1)) "
        "AS candidate WHERE candidate.workspace_id = workspaces.id"
    )
    op.add_column(
        "products",
        sa.Column("search_mode", sa.String(24), server_default="required", nullable=False),
    )
    op.add_column(
        "products", sa.Column("search_mode_decided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "products_search_mode",
        "products",
        "search_mode IN ('connected', 'deferred_reduced', 'required')",
    )
    op.add_column("plan_views", sa.Column("period_start", sa.DateTime(timezone=True)))
    op.add_column("plan_views", sa.Column("period_end", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE plan_views SET period_start = marketing_plans.period_start, "
        "period_end = marketing_plans.period_end FROM marketing_plans "
        "WHERE marketing_plans.id = plan_views.plan_id"
    )
    op.execute(
        "DELETE FROM plan_views WHERE id IN ("
        "SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY workspace_id, product_id, "
        "actor_id, period_start, period_end ORDER BY first_viewed_at, id) AS rank FROM plan_views) "
        "ranked WHERE rank > 1)"
    )
    op.execute(
        "UPDATE plan_views SET report_ordinal = ranked.ordinal, "
        "second_report_return = (ranked.ordinal = 2), "
        "fourth_report_return = (ranked.ordinal = 4) FROM ("
        "SELECT id, row_number() OVER (PARTITION BY workspace_id, product_id, actor_id "
        "ORDER BY period_start, period_end, first_viewed_at, id) AS ordinal FROM plan_views"
        ") ranked WHERE ranked.id = plan_views.id"
    )
    op.alter_column("plan_views", "period_start", nullable=False)
    op.alter_column("plan_views", "period_end", nullable=False)
    op.create_unique_constraint(
        "uq_plan_views_actor_report_period",
        "plan_views",
        ["workspace_id", "product_id", "actor_id", "period_start", "period_end"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_plan_views_actor_report_period", "plan_views", type_="unique")
    op.drop_column("plan_views", "period_end")
    op.drop_column("plan_views", "period_start")
    op.drop_constraint("products_search_mode", "products", type_="check")
    op.drop_column("products", "search_mode_decided_at")
    op.drop_column("products", "search_mode")
    op.drop_constraint("fk_workspaces_activation_product", "workspaces", type_="foreignkey")
    op.drop_column("workspaces", "activation_product_id")
