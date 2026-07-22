from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from app.db.base import Base
from app.domains.legacy import models as legacy_models  # noqa: F401


def test_tenant_parent_child_constraints_are_composite() -> None:
    expected = {
        "github_raw_event_consumers": {"repo_id"},
        "normalized_events": {"raw_event_id", "repo_id"},
        "event_evaluations": {"normalized_event_id", "repo_id"},
        "tweet_candidates": {"normalized_event_id", "repo_id"},
        "generation_runs": {"repo_id", "product_id", "normalized_event_id"},
        "drafts": {
            "repo_id",
            "generation_run_id",
            "selected_candidate_id",
            "normalized_event_id",
        },
        "product_repositories": {"product_id", "repo_id"},
        "achievement_digest_feedback": {"digest_id"},
        "repo_changelog_entries": {"repo_id", "normalized_event_id"},
    }

    for table_name, child_ids in expected.items():
        constraints = [
            constraint
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, ForeignKeyConstraint)
            and "workspace_id" in {column.name for column in constraint.columns}
        ]
        constrained_ids = {
            column.name
            for constraint in constraints
            for column in constraint.columns
            if column.name != "workspace_id"
        }
        assert constrained_ids == child_ids


def test_tenant_parents_expose_composite_candidate_keys() -> None:
    for table_name in (
        "products",
        "repos",
        "github_raw_event_consumers",
        "normalized_events",
        "event_evaluations",
        "tweet_candidates",
        "generation_runs",
        "drafts",
        "achievement_digests",
        "repo_changelog_entries",
    ):
        assert any(
            isinstance(constraint, UniqueConstraint)
            and [column.name for column in constraint.columns] == ["workspace_id", "id"]
            for constraint in Base.metadata.tables[table_name].constraints
        )


def test_raw_events_are_global_restricted_ingress_records() -> None:
    raw_events = Base.metadata.tables["github_raw_events"]

    assert "workspace_id" not in raw_events.c
    assert "payload" not in raw_events.c
    assert {"payload_ciphertext", "payload_key_version"} <= set(raw_events.c.keys())
    assert any(
        isinstance(constraint, UniqueConstraint)
        and [column.name for column in constraint.columns] == ["github_delivery_id"]
        for constraint in raw_events.constraints
    )


def test_raw_event_consumers_bind_workspace_event_and_repo() -> None:
    consumers = Base.metadata.tables["github_raw_event_consumers"]

    assert {
        "workspace_id",
        "raw_event_id",
        "repo_id",
        "user_id",
        "processing_state",
        "processing_started_at",
        "processed_at",
        "created_at",
        "updated_at",
    } <= set(consumers.c.keys())
    assert any(
        isinstance(constraint, UniqueConstraint)
        and [column.name for column in constraint.columns] == ["workspace_id", "raw_event_id"]
        for constraint in consumers.constraints
    )

    normalized_events = Base.metadata.tables["normalized_events"]
    assert any(
        isinstance(constraint, ForeignKeyConstraint)
        and [column.name for column in constraint.columns] == ["workspace_id", "raw_event_id"]
        and [element.target_fullname for element in constraint.elements]
        == [
            "github_raw_event_consumers.workspace_id",
            "github_raw_event_consumers.raw_event_id",
        ]
        for constraint in normalized_events.constraints
    )
