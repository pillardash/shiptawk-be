from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db.base import Base


def test_activation_and_report_period_metadata_matches_migration() -> None:
    workspaces = Base.metadata.tables["workspaces"]
    products = Base.metadata.tables["products"]
    views = Base.metadata.tables["plan_views"]

    assert "activation_product_id" in workspaces.c
    assert {"search_mode", "search_mode_decided_at"}.issubset(products.c.keys())
    assert any(
        isinstance(item, CheckConstraint) and "deferred_reduced" in str(item.sqltext)
        for item in products.constraints
    )
    assert {"period_start", "period_end"}.issubset(views.c.keys())
    assert any(
        isinstance(item, UniqueConstraint) and item.name == "uq_plan_views_actor_report_period"
        for item in views.constraints
    )


def test_activation_migration_backfills_only_single_product_workspaces() -> None:
    migration = Path("alembic/versions/0020_activation_journey.py").read_text(encoding="utf-8")
    assert "HAVING count(*) = 1" in migration
    assert "search_mode IN ('connected', 'deferred_reduced', 'required')" in migration
    assert "uq_plan_views_actor_report_period" in migration
    assert "row_number() OVER (PARTITION BY workspace_id, product_id" in migration
