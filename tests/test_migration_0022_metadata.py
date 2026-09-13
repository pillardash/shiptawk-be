from pathlib import Path

from sqlalchemy import ForeignKeyConstraint, Index

from app.db.base import Base
from app.modules.analytics.models.product_event import ProductEvent  # noqa: F401
from app.modules.operator.models.recommendation_usefulness_feedback import (
    RecommendationUsefulnessFeedback,  # noqa: F401
)
from app.modules.workspaces.models import Workspace  # noqa: F401


def test_corrective_ownership_and_replay_metadata() -> None:
    workspaces = Base.metadata.tables["workspaces"]
    feedback = Base.metadata.tables["recommendation_usefulness_feedback"]
    events = Base.metadata.tables["product_events"]

    activation_fk = next(
        constraint
        for constraint in workspaces.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_workspaces_activation_product_tenant"
    )
    assert [column.name for column in activation_fk.columns] == ["id", "activation_product_id"]
    assert [element.target_fullname for element in activation_fk.elements] == [
        "products.workspace_id",
        "products.id",
    ]
    assert feedback.c.product_id.nullable is False

    actorless_index = next(
        index
        for index in events.indexes
        if isinstance(index, Index) and index.name == "uq_product_events_actorless_key"
    )
    assert actorless_index.unique
    assert [column.name for column in actorless_index.columns] == [
        "workspace_id",
        "event_name",
        "idempotency_key",
    ]
    assert str(actorless_index.dialect_options["postgresql"]["where"]) == "actor_id IS NULL"


def test_corrective_migration_repairs_data_before_enforcing_constraints() -> None:
    migration = (
        Path(__file__).parents[1] / "alembic/versions/0022_tenant_ownership_replay.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0021_repository_evidence"' in migration
    assert "SET activation_product_id = NULL" in migration
    assert "SET product_id = recommendation.product_id" in migration
    assert "feedback.product_id IS NULL OR NOT EXISTS" in migration
    assert "GROUP BY workspace_id, event_name, idempotency_key HAVING count(*) > 1" in migration
    assert 'postgresql_where=sa.text("actor_id IS NULL")' in migration
