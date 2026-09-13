import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.db.base import Base
from app.main import app
from app.modules.analytics.schemas import BrowserProductEventCommand
from app.modules.analytics.services.product_event_service import (
    EventScopeError,
    write_product_event,
)
from app.modules.operator.schemas.weekly_growth_api_schema import (
    RecommendationUsefulnessFeedbackCommand,
)


def test_phase6_tables_have_tenant_and_idempotency_constraints() -> None:
    feedback = Base.metadata.tables["recommendation_usefulness_feedback"]
    views = Base.metadata.tables["plan_views"]
    events = Base.metadata.tables["product_events"]
    assert any(
        isinstance(item, ForeignKeyConstraint) and len(item.columns) == 3
        for item in feedback.constraints
    )
    assert any(
        isinstance(item, ForeignKeyConstraint) and len(item.columns) == 3
        for item in views.constraints
    )
    assert any(
        isinstance(item, UniqueConstraint)
        and {column.name for column in item.columns}
        == {"workspace_id", "actor_id", "idempotency_key"}
        for item in events.constraints
    )
    assert any(
        isinstance(item, CheckConstraint) and "schema_version = 1" in str(item.sqltext)
        for item in events.constraints
    )
    assert any(
        isinstance(item, CheckConstraint) and "source = 'server'" in str(item.sqltext)
        for item in events.constraints
    )
    assert events.c.product_id.nullable
    assert any(
        isinstance(item, CheckConstraint)
        and "github_installation_attached" in str(item.sqltext)
        and "product_id IS NULL" in str(item.sqltext)
        for item in events.constraints
    )
    assert any(
        isinstance(item, ForeignKeyConstraint)
        and [column.name for column in item.columns] == ["workspace_id"]
        for item in events.constraints
    )


def test_feedback_contract_is_strict_and_reason_aware() -> None:
    with pytest.raises(ValidationError):
        RecommendationUsefulnessFeedbackCommand.model_validate(
            {"rating": "useful", "reason": "incorrect"}
        )
    with pytest.raises(ValidationError):
        RecommendationUsefulnessFeedbackCommand.model_validate({"rating": "useful", "metadata": {}})
    assert (
        RecommendationUsefulnessFeedbackCommand.model_validate(
            {"rating": "not_useful", "reason": "too_generic"}
        ).schema_version
        == 1
    )


def test_browser_event_contract_rejects_outcomes_and_arbitrary_metadata() -> None:
    extras: tuple[dict[str, object], ...] = (
        {"metadata": {}},
        {"completed": True},
        {"comment": "private"},
    )
    for extra in extras:
        with pytest.raises(ValidationError):
            BrowserProductEventCommand.model_validate(
                {
                    "eventName": "measurement_viewed",
                    "resourceId": "00000000-0000-0000-0000-000000000001",
                    **extra,
                }
            )


@pytest.mark.asyncio
async def test_event_writer_rejects_invalid_scope_combinations() -> None:
    db = cast(Any, object())
    workspace_id, actor_id, resource_id = uuid4(), uuid4(), uuid4()
    with pytest.raises(EventScopeError):
        await write_product_event(
            db,
            workspace_id=workspace_id,
            product_id=None,
            actor_id=actor_id,
            event_name="product_created",
            idempotency_key="invalid-product-scope",
            resource_type="product",
            resource_id=resource_id,
        )
    with pytest.raises(EventScopeError):
        await write_product_event(
            db,
            workspace_id=workspace_id,
            product_id=uuid4(),
            actor_id=actor_id,
            event_name="github_installation_attached",
            idempotency_key="invalid-workspace-scope",
            resource_type="integration_connection",
            resource_id=resource_id,
        )
    with pytest.raises(EventScopeError):
        await write_product_event(
            db,
            workspace_id=workspace_id,
            product_id=None,
            actor_id=actor_id,
            event_name="repository_monitoring_enabled",
            idempotency_key="invalid-resource-type",
            resource_type="integration_connection",
            resource_id=resource_id,
            resource_revision=1,
        )
    with pytest.raises(EventScopeError):
        await write_product_event(
            db,
            workspace_id=workspace_id,
            product_id=None,
            actor_id=actor_id,
            event_name="github_installation_attached",
            idempotency_key="missing-resource-id",
            resource_type="integration_connection",
            resource_id=None,
        )


def test_live_openapi_matches_committed_contract_and_has_no_sensitive_fields() -> None:
    committed = json.loads(Path("contracts/openapi.json").read_text(encoding="utf-8"))
    assert app.openapi() == committed
    forbidden = {"credential", "credentials", "ciphertext", "rawPrompt", "providerReceipt"}
    exposed = {
        name
        for schema in committed["components"]["schemas"].values()
        for name in schema.get("properties", {})
    }
    assert forbidden.isdisjoint(exposed)
