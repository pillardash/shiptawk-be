import os

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")


@pytest.mark.asyncio
async def test_phase5_product_fks_and_partial_weekly_period_index_exist() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            indexes = await connection.run_sync(
                lambda sync: inspect(sync).get_indexes("operator_runs")
            )
            approval_fks = await connection.run_sync(
                lambda sync: inspect(sync).get_foreign_keys("approval_requests")
            )
            action_fks = await connection.run_sync(
                lambda sync: inspect(sync).get_foreign_keys("plan_actions")
            )
        weekly_index = next(
            index for index in indexes if index["name"] == "uq_operator_runs_weekly_period"
        )
        assert weekly_index["unique"]
        assert "weekly_growth" in str(weekly_index["dialect_options"]["postgresql_where"])
        assert any(
            fk["name"] == "fk_approval_requests_exact_asset_revision"
            and fk["constrained_columns"]
            == ["workspace_id", "product_id", "asset_id", "asset_revision"]
            for fk in approval_fks
        )
        assert any(
            fk["name"] == "fk_plan_actions_plan_product_tenant"
            and fk["constrained_columns"] == ["workspace_id", "product_id", "plan_id"]
            for fk in action_fks
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_phase5_backfill_left_no_resolvable_product_ids_null() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            resolvable_plans = await connection.scalar(
                text(
                    "SELECT count(*) FROM marketing_plans p JOIN operator_runs r "
                    "ON r.workspace_id = p.workspace_id AND r.id = p.operator_run_id "
                    "WHERE p.product_id IS NULL AND r.product_id IS NOT NULL"
                )
            )
            resolvable_actions = await connection.scalar(
                text(
                    "SELECT count(*) FROM plan_actions a JOIN marketing_plans p "
                    "ON p.workspace_id = a.workspace_id AND p.id = a.plan_id "
                    "WHERE a.product_id IS NULL AND p.product_id IS NOT NULL"
                )
            )
            resolvable_approvals = await connection.scalar(
                text(
                    "SELECT count(*) FROM approval_requests ar JOIN plan_actions a "
                    "ON a.workspace_id = ar.workspace_id AND a.id = ar.action_id "
                    "WHERE ar.product_id IS NULL AND a.product_id IS NOT NULL"
                )
            )
        assert (resolvable_plans, resolvable_actions, resolvable_approvals) == (0, 0, 0)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_operator_command_receipt_unique_constraint_exists() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            constraints = await connection.run_sync(
                lambda sync: inspect(sync).get_unique_constraints("operator_command_receipts")
            )
        assert any(
            item["name"] == "uq_operator_command_receipts_command"
            and item["column_names"]
            == ["workspace_id", "product_id", "operation", "resource_id", "idempotency_key"]
            for item in constraints
        )
    finally:
        await engine.dispose()
