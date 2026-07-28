import importlib.util
from pathlib import Path

from sqlalchemy import String, Uuid

from app.db.base import Base
from app.modules.operator.models import (  # noqa: F401
    Opportunity,
    OpportunityEvaluation,
    OpportunityFeedback,
    OpportunityStatusEvent,
)


def test_0011_revision_and_additive_backfills_are_exact() -> None:
    path = Path(__file__).parents[1] / "alembic/versions/0011_operator_opportunity_engine.py"
    spec = importlib.util.spec_from_file_location("migration_0011", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0011_operator_opportunity_engine"
    assert migration.down_revision == "0010_search_intelligence"
    assert migration.LEGACY_RUN_KIND == "legacy_planning"
    assert migration.INITIAL_LIFECYCLE_VERSION == "1"


def test_identity_fingerprint_metadata_supports_prefixed_sha256() -> None:
    for table_name in ("opportunities", "opportunity_evaluations"):
        column = Base.metadata.tables[table_name].c.identity_fingerprint
        assert isinstance(column.type, String)
        assert column.type.length == 71


def test_phase4_migration_metadata_has_uuid_actors_and_retry_key() -> None:
    for table_name in ("opportunity_feedback", "opportunity_status_events"):
        assert isinstance(Base.metadata.tables[table_name].c.actor_id.type, Uuid)
    evaluations = Base.metadata.tables["opportunity_evaluations"]
    assert not evaluations.c.evaluation_key.nullable
    assert any(
        constraint.name == "uq_opportunity_evaluations_run_key"
        for constraint in evaluations.constraints
    )
