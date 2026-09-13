"""enforce tenant ownership and actorless event replay safety

Revision ID: 0022_tenant_ownership
Revises: 0021_repository_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_tenant_ownership"
down_revision: str | None = "0021_repository_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE workspaces SET activation_product_id = NULL "
        "WHERE activation_product_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM products WHERE products.workspace_id = workspaces.id "
        "AND products.id = workspaces.activation_product_id)"
    )
    op.drop_constraint("fk_workspaces_activation_product", "workspaces", type_="foreignkey")
    op.create_foreign_key(
        "fk_workspaces_activation_product_tenant",
        "workspaces",
        "products",
        ["id", "activation_product_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )

    op.execute(
        "UPDATE recommendation_usefulness_feedback AS feedback "
        "SET product_id = recommendation.product_id "
        "FROM operator_recommendations AS recommendation "
        "WHERE feedback.product_id IS NULL "
        "AND recommendation.workspace_id = feedback.workspace_id "
        "AND recommendation.id = feedback.recommendation_id"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT 1 FROM recommendation_usefulness_feedback AS feedback "
        "WHERE feedback.product_id IS NULL OR NOT EXISTS ("
        "SELECT 1 FROM operator_recommendations AS recommendation "
        "WHERE recommendation.workspace_id = feedback.workspace_id "
        "AND recommendation.product_id = feedback.product_id "
        "AND recommendation.id = feedback.recommendation_id)) "
        "THEN RAISE EXCEPTION "
        "'recommendation usefulness feedback has unresolved tenant ownership'; "
        "END IF; END $$"
    )
    op.alter_column("recommendation_usefulness_feedback", "product_id", nullable=False)

    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT 1 FROM product_events WHERE actor_id IS NULL "
        "GROUP BY workspace_id, event_name, idempotency_key HAVING count(*) > 1) "
        "THEN RAISE EXCEPTION 'actorless product events contain duplicate replay keys'; "
        "END IF; END $$"
    )
    op.create_index(
        "uq_product_events_actorless_key",
        "product_events",
        ["workspace_id", "event_name", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("actor_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_product_events_actorless_key", table_name="product_events")
    op.alter_column("recommendation_usefulness_feedback", "product_id", nullable=True)
    op.drop_constraint("fk_workspaces_activation_product_tenant", "workspaces", type_="foreignkey")
    op.create_foreign_key(
        "fk_workspaces_activation_product",
        "workspaces",
        "products",
        ["activation_product_id"],
        ["id"],
        ondelete="SET NULL",
    )
