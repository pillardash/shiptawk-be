"""add weekly operator canonical domain foundations

Revision ID: 0014_weekly_operator_foundations
Revises: 0013_operator_ai_outputs
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_weekly_operator_foundations"
down_revision: str | None = "0013_operator_ai_outputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _add_columns(table: str, columns: Sequence[sa.Column[Any]]) -> None:
    for column in columns:
        op.add_column(table, column)


def upgrade() -> None:
    _add_columns(
        "operator_runs",
        (
            sa.Column("logical_period_start", sa.DateTime(timezone=True)),
            sa.Column("logical_period_end", sa.DateTime(timezone=True)),
            sa.Column("workflow_version", sa.String(64)),
            sa.Column("current_stage", sa.String(32)),
            sa.Column("stage_summary", JSONB),
            sa.Column("finalized_at", sa.DateTime(timezone=True)),
            sa.Column("stopped_reason", sa.String(500)),
        ),
    )
    _add_columns(
        "marketing_plans",
        (
            sa.Column("plan_revision", sa.Integer()),
            sa.Column("schema_version", sa.Integer()),
            sa.Column("selection_policy_version", sa.String(64)),
            sa.Column("finalized_at", sa.DateTime(timezone=True)),
            sa.Column("plan_snapshot", JSONB),
            sa.Column("action_count", sa.Integer()),
            sa.Column("no_recommendation_count", sa.Integer()),
        ),
    )
    _add_columns(
        "plan_actions",
        (
            sa.Column("position", sa.Integer()),
            sa.Column("recommendation_id", sa.Uuid()),
            sa.Column("prepared_output_type", sa.String(32)),
            sa.Column("expected_metric", JSONB),
            sa.Column("measurement_window_days", sa.Integer()),
            sa.Column("estimated_effort", sa.String(16)),
            sa.Column("approved_asset_id", sa.Uuid()),
            sa.Column("approved_asset_revision", sa.Integer()),
            sa.Column("measurement_due_at", sa.DateTime(timezone=True)),
            sa.Column("completed_by_actor_id", sa.Uuid()),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("dismissed_by_actor_id", sa.Uuid()),
            sa.Column("dismissed_at", sa.DateTime(timezone=True)),
            sa.Column("dismissal_reason", sa.Text()),
        ),
    )
    _add_columns(
        "measurement_windows",
        (
            sa.Column("product_id", sa.Uuid()),
            sa.Column("expected_metric", JSONB),
            sa.Column("baseline_period", JSONB),
            sa.Column("baseline_value", JSONB),
            sa.Column("comparison_period", JSONB),
            sa.Column("comparison_value", JSONB),
            sa.Column("due_at", sa.DateTime(timezone=True)),
            sa.Column("collected_at", sa.DateTime(timezone=True)),
            sa.Column("outcome", sa.String(32)),
            sa.Column("unavailable_reason", sa.String(500)),
            sa.Column("limitations", JSONB),
        ),
    )

    # Only unambiguous authoritative chains are backfilled; unresolved legacy rows remain nullable.
    op.execute(
        "UPDATE marketing_plans p SET product_id = r.product_id FROM operator_runs r "
        "WHERE p.product_id IS NULL AND p.operator_run_id = r.id "
        "AND p.workspace_id = r.workspace_id AND r.product_id IS NOT NULL"
    )
    op.execute(
        "UPDATE plan_actions a SET product_id = p.product_id FROM marketing_plans p "
        "WHERE a.product_id IS NULL AND a.plan_id = p.id AND a.workspace_id = p.workspace_id "
        "AND p.product_id IS NOT NULL"
    )
    op.execute(
        "UPDATE plan_actions a SET product_id = o.product_id FROM opportunities o "
        "WHERE a.product_id IS NULL AND a.opportunity_id = o.id "
        "AND a.workspace_id = o.workspace_id "
        "AND o.product_id IS NOT NULL"
    )
    op.execute(
        "UPDATE approval_requests ar SET product_id = a.product_id FROM plan_actions a "
        "WHERE ar.product_id IS NULL AND ar.action_id = a.id AND ar.workspace_id = a.workspace_id "
        "AND a.product_id IS NOT NULL"
    )
    op.execute(
        "UPDATE measurement_windows m SET product_id = a.product_id FROM plan_actions a "
        "WHERE m.action_id = a.id AND m.workspace_id = a.workspace_id AND a.product_id IS NOT NULL"
    )

    with op.batch_alter_table("operator_runs") as batch:
        batch.drop_constraint("operator_runs_run_kind", type_="check")
        batch.create_check_constraint(
            "operator_runs_run_kind",
            "run_kind IN ('legacy_planning','opportunity_detection','weekly_growth')",
        )
        batch.create_check_constraint(
            "operator_runs_weekly_period",
            "run_kind <> 'weekly_growth' OR (product_id IS NOT NULL "
            "AND workflow_version IS NOT NULL "
            "AND logical_period_start IS NOT NULL AND logical_period_end > logical_period_start)",
        )
    op.create_index(
        "uq_operator_runs_weekly_period",
        "operator_runs",
        ["workspace_id", "product_id", "logical_period_start", "workflow_version"],
        unique=True,
        postgresql_where=sa.text("run_kind = 'weekly_growth' AND logical_period_start IS NOT NULL"),
    )
    with op.batch_alter_table("marketing_plans") as batch:
        batch.drop_constraint("marketing_plans_status", type_="check")
        batch.create_check_constraint(
            "marketing_plans_status",
            "status IN ('draft','active','cancelled','preparing','ready','completed','stopped')",
        )
        batch.create_check_constraint(
            "marketing_plans_phase5_shape",
            "status NOT IN ('preparing','ready','stopped') OR (product_id IS NOT NULL AND "
            "plan_revision > 0 AND schema_version > 0)",
        )
        batch.create_foreign_key(
            "fk_marketing_plans_run_product_tenant",
            "operator_runs",
            ["workspace_id", "product_id", "operator_run_id"],
            ["workspace_id", "product_id", "id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("plan_actions") as batch:
        batch.drop_constraint("plan_actions_status", type_="check")
        batch.create_check_constraint(
            "plan_actions_status",
            "status IN ('pending','ready','executing','completed','failed',"
            "'cancelled','dismissed')",
        )
        batch.create_check_constraint(
            "plan_actions_position", "position IS NULL OR position BETWEEN 1 AND 3"
        )
        batch.create_check_constraint(
            "plan_actions_approved_asset_pair",
            "(approved_asset_id IS NULL AND approved_asset_revision IS NULL) OR "
            "(approved_asset_id IS NOT NULL AND approved_asset_revision IS NOT NULL)",
        )
        batch.create_foreign_key(
            "fk_plan_actions_plan_product_tenant",
            "marketing_plans",
            ["workspace_id", "product_id", "plan_id"],
            ["workspace_id", "product_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_plan_actions_recommendation_tenant",
            "operator_recommendations",
            ["workspace_id", "product_id", "recommendation_id"],
            ["workspace_id", "product_id", "id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_plan_actions_completed_actor", "users", ["completed_by_actor_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_plan_actions_dismissed_actor", "users", ["dismissed_by_actor_id"], ["id"]
        )
    op.create_unique_constraint(
        "uq_prepared_assets_tenant_id_revision",
        "prepared_assets",
        ["workspace_id", "product_id", "id", "revision"],
    )
    with op.batch_alter_table("approval_requests") as batch:
        batch.create_check_constraint(
            "approval_requests_asset_revision_pair",
            "(asset_id IS NULL AND asset_revision IS NULL) OR "
            "(asset_id IS NOT NULL AND asset_revision IS NOT NULL)",
        )
        batch.create_foreign_key(
            "fk_approval_requests_action_product_tenant",
            "plan_actions",
            ["workspace_id", "product_id", "action_id"],
            ["workspace_id", "product_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_approval_requests_exact_asset_revision",
            "prepared_assets",
            ["workspace_id", "product_id", "asset_id", "asset_revision"],
            ["workspace_id", "product_id", "id", "revision"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("measurement_windows") as batch:
        batch.drop_constraint("measurement_windows_status", type_="check")
        batch.create_check_constraint(
            "measurement_windows_status",
            "status IN ('scheduled','measuring','completed','cancelled','unavailable')",
        )
        batch.create_foreign_key(
            "fk_measurement_windows_action_product_tenant",
            "plan_actions",
            ["workspace_id", "product_id", "action_id"],
            ["workspace_id", "product_id", "id"],
            ondelete="CASCADE",
        )
    op.create_index("ix_measurement_windows_product_id", "measurement_windows", ["product_id"])

    op.create_table(
        "action_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("previous_status", sa.String(20)),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("details", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_action_events"),
        sa.UniqueConstraint("workspace_id", "product_id", "id", name="uq_action_events_tenant_id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "action_id"],
            ["plan_actions.workspace_id", "plan_actions.product_id", "plan_actions.id"],
            name="fk_action_events_action_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_action_events_workspace_id_workspaces",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_action_events_actor_id_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_action_events_action_id", "action_events", ["action_id"])
    op.execute(
        "CREATE FUNCTION reject_action_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'action_events is append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER action_events_append_only BEFORE UPDATE OR DELETE ON action_events "
        "FOR EACH ROW EXECUTE FUNCTION reject_action_event_mutation()"
    )
    op.create_table(
        "weekly_growth_deliveries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(64)),
        sa.Column("provider_delivery_id", sa.String(255)),
        sa.Column("provider_receipt", JSONB),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_weekly_growth_deliveries_tenant_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "idempotency_key",
            name="uq_weekly_growth_deliveries_delivery_key",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "plan_id"],
            ["marketing_plans.workspace_id", "marketing_plans.product_id", "marketing_plans.id"],
            name="fk_weekly_growth_deliveries_plan_tenant",
            ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    op.drop_table("weekly_growth_deliveries")
    op.execute("DROP TRIGGER action_events_append_only ON action_events")
    op.drop_table("action_events")
    op.execute("DROP FUNCTION reject_action_event_mutation()")

    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_constraint("fk_approval_requests_exact_asset_revision", type_="foreignkey")
        batch.drop_constraint("fk_approval_requests_action_product_tenant", type_="foreignkey")
        batch.drop_constraint("approval_requests_asset_revision_pair", type_="check")

    op.drop_index("ix_measurement_windows_product_id", table_name="measurement_windows")
    with op.batch_alter_table("measurement_windows") as batch:
        batch.drop_constraint("fk_measurement_windows_action_product_tenant", type_="foreignkey")
        batch.drop_constraint("measurement_windows_status", type_="check")
    op.execute("UPDATE measurement_windows SET status = 'cancelled' WHERE status = 'unavailable'")
    with op.batch_alter_table("measurement_windows") as batch:
        batch.create_check_constraint(
            "measurement_windows_status",
            "status IN ('scheduled', 'measuring', 'completed', 'cancelled')",
        )

    with op.batch_alter_table("plan_actions") as batch:
        for constraint in (
            "fk_plan_actions_dismissed_actor",
            "fk_plan_actions_completed_actor",
            "fk_plan_actions_recommendation_tenant",
            "fk_plan_actions_plan_product_tenant",
        ):
            batch.drop_constraint(constraint, type_="foreignkey")
        batch.drop_constraint("plan_actions_approved_asset_pair", type_="check")
        batch.drop_constraint("plan_actions_position", type_="check")
        batch.drop_constraint("plan_actions_status", type_="check")
    op.execute("UPDATE plan_actions SET status = 'cancelled' WHERE status = 'dismissed'")
    with op.batch_alter_table("plan_actions") as batch:
        batch.create_check_constraint(
            "plan_actions_status",
            "status IN ('pending', 'ready', 'executing', 'completed', 'failed', 'cancelled')",
        )

    op.drop_constraint("uq_prepared_assets_tenant_id_revision", "prepared_assets", type_="unique")

    with op.batch_alter_table("marketing_plans") as batch:
        batch.drop_constraint("fk_marketing_plans_run_product_tenant", type_="foreignkey")
        batch.drop_constraint("marketing_plans_phase5_shape", type_="check")
        batch.drop_constraint("marketing_plans_status", type_="check")
    op.execute(
        "UPDATE marketing_plans SET status = CASE "
        "WHEN status = 'preparing' THEN 'draft' "
        "WHEN status = 'ready' THEN 'active' ELSE 'cancelled' END "
        "WHERE status IN ('preparing', 'ready', 'stopped')"
    )
    with op.batch_alter_table("marketing_plans") as batch:
        batch.create_check_constraint(
            "marketing_plans_status",
            "status IN ('draft', 'active', 'completed', 'cancelled')",
        )

    op.drop_index("uq_operator_runs_weekly_period", table_name="operator_runs")
    with op.batch_alter_table("operator_runs") as batch:
        batch.drop_constraint("operator_runs_weekly_period", type_="check")
        batch.drop_constraint("operator_runs_run_kind", type_="check")
    op.execute(
        "UPDATE operator_runs SET run_kind = 'legacy_planning' WHERE run_kind = 'weekly_growth'"
    )
    with op.batch_alter_table("operator_runs") as batch:
        batch.create_check_constraint(
            "operator_runs_run_kind",
            "run_kind IN ('legacy_planning', 'opportunity_detection')",
        )

    for table, names in (
        (
            "measurement_windows",
            (
                "product_id",
                "expected_metric",
                "baseline_period",
                "baseline_value",
                "comparison_period",
                "comparison_value",
                "due_at",
                "collected_at",
                "outcome",
                "unavailable_reason",
                "limitations",
            ),
        ),
        (
            "plan_actions",
            (
                "position",
                "recommendation_id",
                "prepared_output_type",
                "expected_metric",
                "measurement_window_days",
                "estimated_effort",
                "approved_asset_id",
                "approved_asset_revision",
                "measurement_due_at",
                "completed_by_actor_id",
                "completed_at",
                "dismissed_by_actor_id",
                "dismissed_at",
                "dismissal_reason",
            ),
        ),
        (
            "marketing_plans",
            (
                "plan_revision",
                "schema_version",
                "selection_policy_version",
                "finalized_at",
                "plan_snapshot",
                "action_count",
                "no_recommendation_count",
            ),
        ),
        (
            "operator_runs",
            (
                "logical_period_start",
                "logical_period_end",
                "workflow_version",
                "current_stage",
                "stage_summary",
                "finalized_at",
                "stopped_reason",
            ),
        ),
    ):
        for name in names:
            op.drop_column(table, name)
