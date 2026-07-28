from collections.abc import Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID


class DomainConflictError(ValueError):
    pass


def _transition(current: str, target: str, allowed: Mapping[str, frozenset[str]]) -> str:
    if target == current:
        return target
    if target not in allowed.get(current, frozenset()):
        raise DomainConflictError(f"Invalid transition from {current!r} to {target!r}.")
    return target


def transition_run(current: str, target: str) -> str:
    return _transition(
        current,
        target,
        {
            "pending": frozenset({"running", "stopped", "failed"}),
            "running": frozenset({"completed", "stopped", "failed"}),
        },
    )


def transition_plan(current: str, target: str) -> str:
    return _transition(
        current,
        target,
        {
            "preparing": frozenset({"ready", "stopped"}),
            "ready": frozenset({"completed", "stopped"}),
        },
    )


def transition_action(current: str, target: str) -> str:
    return _transition(
        current,
        target,
        {
            "pending": frozenset({"ready", "dismissed"}),
            "ready": frozenset({"executing", "dismissed"}),
            "executing": frozenset({"completed", "failed", "dismissed"}),
            "failed": frozenset({"ready", "dismissed"}),
        },
    )


def select_recommendations(
    candidates: Sequence[dict[str, Any]], *, limit: int = 3
) -> list[dict[str, Any]]:
    if not 0 <= limit <= 3:
        raise ValueError("Weekly plans may select between zero and three recommendations.")
    return sorted(
        candidates,
        key=lambda item: (-float(item.get("priorityScore", 0)), str(item["id"])),
    )[:limit]


def scheduled_weekly_key(product_id: UUID, period_start: datetime, workflow_version: str) -> str:
    if not workflow_version.strip():
        raise ValueError("workflow_version is required.")
    return f"weekly:{product_id}:{period_start.isoformat()}:{workflow_version}"


def assert_product_scope(expected: UUID, actual: UUID | None) -> None:
    if actual is None or expected != actual:
        raise DomainConflictError("Resource does not belong to the requested product.")


def assert_exact_asset_revision(
    approved_asset_id: UUID | None,
    approved_revision: int | None,
    current_asset_id: UUID,
    current_revision: int,
) -> None:
    if approved_asset_id != current_asset_id or approved_revision != current_revision:
        raise DomainConflictError("Approval does not cover the exact current asset revision.")


def invalidate_approval_after_edit(
    status: str, approved_revision: int, current_revision: int
) -> str:
    return "pending" if status == "approved" and approved_revision != current_revision else status


def build_plan_snapshot(
    *,
    run_id: UUID,
    plan_id: UUID,
    revision: int,
    actions: Sequence[Mapping[str, object]],
    no_recommendations: Sequence[Mapping[str, object]],
    shipped_summary: Mapping[str, object],
    search_summary: Mapping[str, object],
) -> Mapping[str, object]:
    ordered = sorted(actions, key=lambda action: (str(action["position"]), str(action["id"])))
    snapshot: dict[str, object] = {
        "schemaVersion": 1,
        "runId": str(run_id),
        "planId": str(plan_id),
        "revision": revision,
        "actionIds": [str(action["id"]) for action in ordered],
        "recommendationIds": [str(action["recommendationId"]) for action in ordered],
        "noRecommendationOutcomes": [dict(outcome) for outcome in no_recommendations],
        "shippedSummary": dict(shipped_summary),
        "searchSummary": dict(search_summary),
    }
    return MappingProxyType(snapshot)
