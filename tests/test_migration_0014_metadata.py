import importlib.util
from pathlib import Path

from app.db.base import Base
from app.modules.operator.models import (  # noqa: F401
    ActionEvent,
    WeeklyGrowthDelivery,
)


def test_0014_is_additive_to_shipped_0013() -> None:
    path = Path(__file__).parents[1] / "alembic/versions/0014_weekly_operator_foundations.py"
    spec = importlib.util.spec_from_file_location("migration_0014", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0014_weekly_operator_foundations"
    assert migration.down_revision == "0013_operator_ai_outputs"


def test_0014_downgrade_removes_all_added_database_objects_before_columns() -> None:
    source = (
        Path(__file__).parents[1] / "alembic/versions/0014_weekly_operator_foundations.py"
    ).read_text()
    downgrade = source.split("def downgrade() -> None:", maxsplit=1)[1]

    names = (
        "action_events_append_only",
        "reject_action_event_mutation",
        "fk_approval_requests_exact_asset_revision",
        "fk_approval_requests_action_product_tenant",
        "approval_requests_asset_revision_pair",
        "ix_measurement_windows_product_id",
        "fk_measurement_windows_action_product_tenant",
        "fk_plan_actions_dismissed_actor",
        "fk_plan_actions_completed_actor",
        "fk_plan_actions_recommendation_tenant",
        "fk_plan_actions_plan_product_tenant",
        "plan_actions_approved_asset_pair",
        "plan_actions_position",
        "uq_prepared_assets_tenant_id_revision",
        "fk_marketing_plans_run_product_tenant",
        "marketing_plans_phase5_shape",
        "uq_operator_runs_weekly_period",
        "operator_runs_weekly_period",
    )
    for name in names:
        assert name in downgrade

    first_column_drop = downgrade.index("op.drop_column")
    assert all(downgrade.index(name) < first_column_drop for name in names)


def test_0014_downgrade_restores_0013_check_definitions() -> None:
    source = (
        Path(__file__).parents[1] / "alembic/versions/0014_weekly_operator_foundations.py"
    ).read_text()
    downgrade = source.split("def downgrade() -> None:", maxsplit=1)[1]

    assert "run_kind IN ('legacy_planning', 'opportunity_detection')" in downgrade
    assert "status IN ('draft', 'active', 'completed', 'cancelled')" in downgrade
    assert (
        "status IN ('pending', 'ready', 'executing', 'completed', 'failed', 'cancelled')"
        in downgrade
    )
    assert "status IN ('scheduled', 'measuring', 'completed', 'cancelled')" in downgrade
    assert "SET run_kind = 'legacy_planning' WHERE run_kind = 'weekly_growth'" in downgrade
    assert "SET status = 'cancelled' WHERE status = 'dismissed'" in downgrade
    assert "SET status = 'cancelled' WHERE status = 'unavailable'" in downgrade
    assert "WHERE status IN ('preparing', 'ready', 'stopped')" in downgrade


def test_phase5_models_are_product_scoped_and_append_only() -> None:
    events = Base.metadata.tables["action_events"]
    deliveries = Base.metadata.tables["weekly_growth_deliveries"]
    assert not events.c.product_id.nullable
    assert not deliveries.c.product_id.nullable
    assert any(c.name == "uq_action_events_tenant_id" for c in events.constraints)
    assert any(c.name == "uq_weekly_growth_deliveries_delivery_key" for c in deliveries.constraints)


def test_legacy_product_columns_remain_nullable_in_metadata() -> None:
    for table in ("marketing_plans", "plan_actions", "approval_requests"):
        assert Base.metadata.tables[table].c.product_id.nullable
