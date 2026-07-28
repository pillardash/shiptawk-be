"""add operator AI canonical outputs

Revision ID: 0013_operator_ai_outputs
Revises: 0012_llm_executions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_operator_ai_outputs"
down_revision: str | None = "0012_llm_executions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def tenant_product_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id", "product_id"],
        ["products.workspace_id", "products.id"],
        name=name,
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.add_column("marketing_plans", sa.Column("product_id", sa.Uuid(), nullable=True))
    op.add_column("plan_actions", sa.Column("product_id", sa.Uuid(), nullable=True))
    op.add_column("plan_actions", sa.Column("llm_execution_id", sa.Uuid(), nullable=True))
    op.add_column("approval_requests", sa.Column("product_id", sa.Uuid(), nullable=True))
    op.add_column("approval_requests", sa.Column("asset_id", sa.Uuid(), nullable=True))
    op.add_column("approval_requests", sa.Column("asset_revision", sa.Integer(), nullable=True))
    op.create_index("ix_marketing_plans_product_id", "marketing_plans", ["product_id"])
    op.create_index("ix_plan_actions_product_id", "plan_actions", ["product_id"])
    op.create_index("ix_plan_actions_llm_execution_id", "plan_actions", ["llm_execution_id"])
    op.create_index("ix_approval_requests_product_id", "approval_requests", ["product_id"])
    op.create_index("ix_approval_requests_asset_id", "approval_requests", ["asset_id"])
    op.create_unique_constraint(
        "uq_marketing_plans_tenant_id", "marketing_plans", ["workspace_id", "product_id", "id"]
    )
    op.create_unique_constraint(
        "uq_plan_actions_tenant_id", "plan_actions", ["workspace_id", "product_id", "id"]
    )
    op.create_foreign_key(
        "fk_marketing_plans_product_tenant",
        "marketing_plans",
        "products",
        ["workspace_id", "product_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_plan_actions_product_tenant",
        "plan_actions",
        "products",
        ["workspace_id", "product_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_plan_actions_llm_execution_tenant",
        "plan_actions",
        "llm_executions",
        ["workspace_id", "product_id", "llm_execution_id"],
        ["workspace_id", "product_id", "id"],
    )
    op.create_table(
        "operator_recommendations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("operator_run_id", sa.Uuid(), nullable=False),
        sa.Column("llm_execution_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("output", JSONB, nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operator_recommendations"),
        tenant_product_fk("fk_operator_recommendations_product_tenant"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "opportunity_id"],
            ["opportunities.workspace_id", "opportunities.product_id", "opportunities.id"],
            name="fk_operator_recommendations_opportunity_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.product_id", "operator_runs.id"],
            name="fk_operator_recommendations_operator_run_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "llm_execution_id"],
            ["llm_executions.workspace_id", "llm_executions.product_id", "llm_executions.id"],
            name="fk_operator_recommendations_llm_execution_tenant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_operator_recommendations_tenant_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "opportunity_id",
            "idempotency_key",
            name="uq_operator_recommendations_opportunity_key",
        ),
        sa.CheckConstraint(
            "kind IN ('recommendation','no_recommendation')",
            name="operator_recommendations_kind",
        ),
    )
    op.create_table(
        "operator_recommendation_evidence",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "recommendation_id"],
            [
                "operator_recommendations.workspace_id",
                "operator_recommendations.product_id",
                "operator_recommendations.id",
            ],
            name="fk_operator_recommendation_evidence_recommendation_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "evidence_id"],
            [
                "opportunity_evidence.workspace_id",
                "opportunity_evidence.product_id",
                "opportunity_evidence.id",
            ],
            name="fk_operator_recommendation_evidence_evidence_tenant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "recommendation_id",
            "evidence_id",
            name="uq_operator_recommendation_evidence_link",
        ),
    )
    op.create_table(
        "prepared_assets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid()),
        sa.Column("llm_execution_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("structured_content", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_prepared_assets"),
        tenant_product_fk("fk_prepared_assets_product_tenant"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "recommendation_id"],
            [
                "operator_recommendations.workspace_id",
                "operator_recommendations.product_id",
                "operator_recommendations.id",
            ],
            name="fk_prepared_assets_recommendation_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "llm_execution_id"],
            ["llm_executions.workspace_id", "llm_executions.product_id", "llm_executions.id"],
            name="fk_prepared_assets_llm_execution_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "action_id"],
            ["plan_actions.workspace_id", "plan_actions.product_id", "plan_actions.id"],
            name="fk_prepared_assets_action_tenant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_prepared_assets_tenant_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "recommendation_id",
            "revision",
            name="uq_prepared_assets_recommendation_revision",
        ),
        sa.CheckConstraint("revision > 0", name="prepared_assets_revision"),
        sa.CheckConstraint(
            "kind IN ('seo_page_update','blog_brief','product_update','x_draft','no_asset')",
            name="prepared_assets_kind",
        ),
    )
    op.create_table(
        "action_risk_reviews",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("asset_revision", sa.Integer(), nullable=False),
        sa.Column("llm_execution_id", sa.Uuid(), nullable=False),
        sa.Column("deterministic_outcome", sa.String(16), nullable=False),
        sa.Column("ai_outcome", sa.String(16), nullable=False),
        sa.Column("final_outcome", sa.String(16), nullable=False),
        sa.Column("findings", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_action_risk_reviews"),
        tenant_product_fk("fk_action_risk_reviews_product_tenant"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "asset_id"],
            ["prepared_assets.workspace_id", "prepared_assets.product_id", "prepared_assets.id"],
            name="fk_action_risk_reviews_asset_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "llm_execution_id"],
            ["llm_executions.workspace_id", "llm_executions.product_id", "llm_executions.id"],
            name="fk_action_risk_reviews_llm_execution_tenant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_action_risk_reviews_tenant_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "asset_id",
            "asset_revision",
            name="uq_action_risk_reviews_asset_revision",
        ),
        sa.CheckConstraint(
            "final_outcome IN ('pass','review','block')", name="action_risk_reviews_outcome"
        ),
    )
    op.create_foreign_key(
        "fk_approval_requests_asset_tenant",
        "approval_requests",
        "prepared_assets",
        ["workspace_id", "product_id", "asset_id"],
        ["workspace_id", "product_id", "id"],
        ondelete="RESTRICT",
    )
    op.execute(
        "CREATE FUNCTION reject_action_risk_review_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'action_risk_reviews is append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER action_risk_reviews_append_only BEFORE UPDATE OR DELETE ON "
        "action_risk_reviews FOR EACH ROW EXECUTE FUNCTION reject_action_risk_review_mutation()"
    )


def downgrade() -> None:
    op.drop_constraint("fk_approval_requests_asset_tenant", "approval_requests", type_="foreignkey")
    op.drop_table("action_risk_reviews")
    op.execute("DROP FUNCTION reject_action_risk_review_mutation()")
    op.drop_table("prepared_assets")
    op.drop_table("operator_recommendation_evidence")
    op.drop_table("operator_recommendations")
    op.drop_constraint("fk_plan_actions_llm_execution_tenant", "plan_actions", type_="foreignkey")
    op.drop_constraint("fk_plan_actions_product_tenant", "plan_actions", type_="foreignkey")
    op.drop_constraint("fk_marketing_plans_product_tenant", "marketing_plans", type_="foreignkey")
    op.drop_constraint("uq_plan_actions_tenant_id", "plan_actions", type_="unique")
    op.drop_constraint("uq_marketing_plans_tenant_id", "marketing_plans", type_="unique")
    op.drop_index("ix_approval_requests_asset_id", table_name="approval_requests")
    op.drop_index("ix_approval_requests_product_id", table_name="approval_requests")
    op.drop_index("ix_plan_actions_llm_execution_id", table_name="plan_actions")
    op.drop_index("ix_plan_actions_product_id", table_name="plan_actions")
    op.drop_index("ix_marketing_plans_product_id", table_name="marketing_plans")
    op.drop_column("approval_requests", "asset_revision")
    op.drop_column("approval_requests", "asset_id")
    op.drop_column("approval_requests", "product_id")
    op.drop_column("plan_actions", "llm_execution_id")
    op.drop_column("plan_actions", "product_id")
    op.drop_column("marketing_plans", "product_id")
