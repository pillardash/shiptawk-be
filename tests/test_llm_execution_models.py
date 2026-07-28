from pathlib import Path
from typing import cast

from sqlalchemy import Table, UniqueConstraint

from app.modules.llm.models.llm_execution import LLMExecution
from app.modules.llm.models.llm_execution_attempt import LLMExecutionAttempt


def test_execution_ledger_has_tenant_idempotency_and_no_raw_content_columns() -> None:
    table = cast(Table, LLMExecution.__table__)
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("workspace_id", "product_id", "use_case", "idempotency_key") in unique_columns
    assert "validated_output_snapshot" in table.columns
    assert not ({"prompt", "output", "messages", "raw_body"} & set(table.columns.keys()))
    assert not (
        {"prompt", "output", "messages", "raw_body"}
        & set(LLMExecutionAttempt.__table__.columns.keys())
    )


def test_migration_0012_metadata_and_append_only_attempts() -> None:
    path = Path(__file__).parents[1] / "alembic/versions/0012_llm_executions.py"
    source = path.read_text()

    assert 'revision: str = "0012_llm_executions"' in source
    assert 'down_revision: str | None = "0011_operator_opportunity_engine"' in source
    assert '"llm_executions"' in source
    assert '"llm_execution_attempts"' in source
    assert "request_fingerprint" in source
    assert "route_snapshot" in source
    assert "validated_output_snapshot" in source
    assert "raw_prompt" not in source
