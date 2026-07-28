from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.operator.policies.lexical_match_policy import (
    match_capability,
    query_profile_match,
)
from app.modules.operator.schemas import (
    DetectionInput,
    ProductProfileSnapshot,
    ProfileCapabilitySnapshot,
    WebsiteSnapshot,
)
from app.modules.operator.services.opportunity_snapshot_service import (
    InvalidShippingEvaluationOutcomeError,
    SnapshotHashMismatchError,
    _profile_capability,
    _shipping_outcome,
    canonicalize_detection_input,
    persist_run_snapshot,
)

NOW = datetime(2026, 7, 27, tzinfo=UTC)


def detection_input(*, impressions: int = 10) -> DetectionInput:
    return DetectionInput.model_validate(
        {
            "schema_version": "1",
            "workspace_id": uuid4(),
            "product_id": uuid4(),
            "as_of": NOW,
            "profile": {
                "objective": "Grow signups",
                "profile_version": 2,
                "capabilities": [
                    {"capability_key": "z", "name": "Z"},
                    {"capability_key": "a", "name": "A"},
                ],
            },
            "search": {
                "current": {
                    "clicks": 1,
                    "impressions": impressions,
                    "ctr": Decimal(1) / impressions,
                    "average_position": "4.5",
                },
                "prior": {"clicks": 0, "impressions": 0, "ctr": "0", "average_position": "0"},
                "warnings": ["z", "a"],
            },
        }
    )


def test_canonical_hash_is_stable_and_metric_sensitive() -> None:
    first = canonicalize_detection_input(detection_input())
    second = canonicalize_detection_input(detection_input())
    # Tenant IDs are intentionally part of the immutable input.
    second_input = second.detection_input.model_copy(
        update={
            "workspace_id": first.detection_input.workspace_id,
            "product_id": first.detection_input.product_id,
        }
    )
    second = canonicalize_detection_input(second_input)
    assert first.payload["search"]["current"]["average_position"] == "4.5"  # type: ignore[index]
    assert first.payload["search"]["warnings"] == ["a", "z"]  # type: ignore[index]
    assert first.input_hash == second.input_hash

    changed = detection_input(impressions=11).model_copy(
        update={
            "workspace_id": first.detection_input.workspace_id,
            "product_id": first.detection_input.product_id,
        }
    )
    assert canonicalize_detection_input(changed).input_hash != first.input_hash


class FlushOnlySession:
    def __init__(self) -> None:
        self.added: object | None = None
        self.flushed = False

    def add(self, value: object) -> None:
        self.added = value

    async def flush(self) -> None:
        self.flushed = True


async def test_snapshot_persistence_flushes_without_commit_and_checks_tenant_and_hash() -> None:
    assembly = canonicalize_detection_input(detection_input())
    db = FlushOnlySession()
    snapshot = await persist_run_snapshot(
        db,
        workspace_id=assembly.detection_input.workspace_id,
        product_id=assembly.detection_input.product_id,
        operator_run_id=uuid4(),
        assembly=assembly,
    )
    assert db.flushed
    assert snapshot.retention_expires_at == NOW + timedelta(days=180)

    with pytest.raises(SnapshotHashMismatchError):
        await persist_run_snapshot(
            db,
            workspace_id=assembly.detection_input.workspace_id,
            product_id=assembly.detection_input.product_id,
            operator_run_id=uuid4(),
            assembly=assembly.model_copy(update={"input_hash": "0" * 64}),
        )


def test_profile_objective_remains_required() -> None:
    with pytest.raises(ValidationError):
        DetectionInput.model_validate(
            {
                "schema_version": "1",
                "workspace_id": uuid4(),
                "product_id": uuid4(),
                "as_of": NOW,
                "profile": {"profile_version": 1},
            }
        )


def test_nullable_indexability_and_semantically_ordered_headings_survive_canonicalization() -> None:
    value = detection_input().model_copy(
        update={
            "website": WebsiteSnapshot.model_validate(
                {
                    "pages": [
                        {
                            "page_fingerprint": "page",
                            "url": "https://example.test",
                            "indexable": None,
                            "headings": ["Second", "First"],
                        }
                    ]
                }
            )
        }
    )
    validated = DetectionInput.model_validate(value.model_dump())

    payload = canonicalize_detection_input(validated).payload

    assert payload["website"]["pages"][0]["indexable"] is None  # type: ignore[index]
    assert payload["website"]["pages"][0]["headings"] == ["Second", "First"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("persisted", "snapshot"),
    [("generate", "generate"), ("ignore", "ignore"), ("hold", "review"), ("block", "review")],
)
def test_shipping_evaluation_outcomes_are_normalized(persisted: str, snapshot: str) -> None:
    assert _shipping_outcome(persisted) == snapshot


def test_unknown_shipping_evaluation_outcome_is_a_controlled_failure() -> None:
    with pytest.raises(InvalidShippingEvaluationOutcomeError, match="Unsupported persisted"):
        _shipping_outcome("invalid")


def test_structured_profile_capability_preserves_exact_fields() -> None:
    capability = _profile_capability(
        {
            "capability_key": "automation",
            "name": "Automation",
            "description": "Runs recurring work",
            "active": False,
        }
    )

    assert capability.model_dump() == {
        "capability_key": "automation",
        "name": "Automation",
        "description": "Runs recurring work",
        "active": False,
    }


def test_lexical_capability_match_is_unique_and_abstains_on_ties() -> None:
    export = ProfileCapabilitySnapshot(
        capability_key="export", name="CSV export", description="download reports"
    )
    match = match_capability("Download reports with CSV export", (export,))
    assert match.capability_key == "export"
    assert match.coverage == 1
    assert match.relevance >= Decimal(".20")
    tied = export.model_copy(update={"capability_key": "other"})
    assert match_capability("CSV export download reports", (export, tied)).capability_key is None


def test_query_profile_match_uses_product_and_profile_text_conservatively() -> None:
    profile = ProductProfileSnapshot(
        product_name="Acme Orbit",
        objective="Grow automated reporting",
        audience="marketing teams",
        positioning="Fast analytics",
        capabilities=(ProfileCapabilitySnapshot(capability_key="reports", name="Reports"),),
    )
    relevant = query_profile_match("automated marketing reports", profile)
    assert relevant.relevance >= Decimal(".60")
    assert not relevant.branded and not relevant.navigational
    navigation = query_profile_match("Acme Orbit login", profile)
    assert navigation.branded and navigation.navigational
    assert query_profile_match("unrelated weather", profile).relevance == 0
