from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from app.db.base import Base
from app.modules.drafts import models as draft_models  # noqa: F401
from app.modules.operator import models as operator_models  # noqa: F401
from app.modules.operator.policies.weekly_growth_policy import (
    DomainConflictError,
    assert_exact_asset_revision,
    assert_product_scope,
    build_plan_snapshot,
    invalidate_approval_after_edit,
    scheduled_weekly_key,
    select_recommendations,
    transition_action,
    transition_plan,
    transition_run,
)


def _candidate(score: int, key: str) -> dict[str, object]:
    return {"id": key, "priorityScore": score, "recommendationId": f"rec-{key}"}


@pytest.mark.parametrize(
    ("candidates", "expected"),
    [
        ([], []),
        ([_candidate(1, "a")], ["a"]),
        ([_candidate(1, "a"), _candidate(3, "c"), _candidate(2, "b")], ["c", "b", "a"]),
        (
            [_candidate(4, "d"), _candidate(2, "b"), _candidate(3, "c"), _candidate(1, "a")],
            ["d", "c", "b"],
        ),
    ],
)
def test_selection_is_deterministic_and_bounded(
    candidates: list[dict[str, object]], expected: list[str]
) -> None:
    assert [item["id"] for item in select_recommendations(candidates)] == expected


def test_selection_uses_stable_id_to_break_score_ties() -> None:
    assert [
        item["id"] for item in select_recommendations([_candidate(5, "b"), _candidate(5, "a")])
    ] == [
        "a",
        "b",
    ]


@pytest.mark.parametrize(
    ("policy", "current", "target"),
    [
        (transition_run, "pending", "running"),
        (transition_run, "running", "completed"),
        (transition_plan, "preparing", "ready"),
        (transition_plan, "ready", "completed"),
        (transition_action, "pending", "ready"),
        (transition_action, "ready", "executing"),
        (transition_action, "executing", "completed"),
    ],
)
def test_transition_matrix_accepts_canonical_paths(
    policy: object, current: str, target: str
) -> None:
    assert policy(current, target) == target  # type: ignore[operator]


@pytest.mark.parametrize(
    ("policy", "current", "target"),
    [
        (transition_run, "completed", "running"),
        (transition_plan, "ready", "preparing"),
        (transition_action, "completed", "executing"),
        (transition_action, "dismissed", "ready"),
    ],
)
def test_transition_matrix_rejects_regressions(policy: object, current: str, target: str) -> None:
    with pytest.raises(DomainConflictError):
        policy(current, target)  # type: ignore[operator]


def test_scheduled_key_is_stable_and_product_qualified() -> None:
    product_id = uuid4()
    period = datetime(2026, 7, 27, tzinfo=UTC)
    assert scheduled_weekly_key(product_id, period, "weekly-v1") == (
        f"weekly:{product_id}:2026-07-27T00:00:00+00:00:weekly-v1"
    )


def test_cross_product_links_are_rejected() -> None:
    with pytest.raises(DomainConflictError):
        assert_product_scope(uuid4(), uuid4())


def test_ready_snapshot_is_canonical_and_frozen() -> None:
    run_id, plan_id = uuid4(), uuid4()
    snapshot = build_plan_snapshot(
        run_id=run_id,
        plan_id=plan_id,
        revision=2,
        actions=[
            {"id": "b", "position": 2, "recommendationId": "rb"},
            {"id": "a", "position": 1, "recommendationId": "ra"},
        ],
        no_recommendations=[{"opportunityId": "o1", "reason": "insufficient_evidence"}],
        shipped_summary={"ref": "digest-1"},
        search_summary={"ref": "search-1"},
    )
    assert snapshot["schemaVersion"] == 1
    assert snapshot["actionIds"] == ["a", "b"]
    assert snapshot["recommendationIds"] == ["ra", "rb"]
    with pytest.raises(TypeError):
        snapshot["revision"] = 3  # type: ignore[index]


def test_approval_requires_exact_asset_revision_and_edit_invalidates_it() -> None:
    asset_id = uuid4()
    assert_exact_asset_revision(asset_id, 2, asset_id, 2)
    with pytest.raises(DomainConflictError):
        assert_exact_asset_revision(asset_id, 2, asset_id, 3)
    assert invalidate_approval_after_edit("approved", 2, 3) == "pending"
    assert invalidate_approval_after_edit("approved", 2, 2) == "approved"


def test_phase5_delivery_metadata_has_exact_tenant_constraints() -> None:
    drafts = Base.metadata.tables["drafts"]
    attempts = Base.metadata.tables["publication_attempts"]

    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_drafts_prepared_asset_revision"
        for constraint in drafts.constraints
    )
    assert any(
        isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_drafts_prepared_asset_revision"
        and constraint.ondelete == "RESTRICT"
        for constraint in drafts.constraints
    )

    attempt_foreign_keys = {
        constraint.name: constraint
        for constraint in attempts.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert set(attempt_foreign_keys) == {
        "fk_publication_attempts_workspace_id_workspaces",
        "fk_publication_attempts_draft_tenant",
    }
    assert attempt_foreign_keys["fk_publication_attempts_draft_tenant"].ondelete == "RESTRICT"
    assert {index.name for index in attempts.indexes} == {"ix_publication_attempts_workspace_id"}


def test_phase5_generated_indexes_and_fk_delete_rules_match_migrations() -> None:
    assert "ix_action_events_action_id" in {
        index.name for index in Base.metadata.tables["action_events"].indexes
    }
    assert "ix_measurement_windows_product_id" in {
        index.name for index in Base.metadata.tables["measurement_windows"].indexes
    }

    expected = {
        ("approval_requests", "fk_approval_requests_action_product_tenant"): "CASCADE",
        ("marketing_plans", "fk_marketing_plans_run_product_tenant"): "RESTRICT",
        ("plan_actions", "fk_plan_actions_recommendation_tenant"): "RESTRICT",
    }
    for (table_name, constraint_name), ondelete in expected.items():
        constraints = {
            constraint.name: constraint
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        assert constraints[constraint_name].ondelete == ondelete
