from collections.abc import Mapping
from typing import Any

from app.modules.workspaces.enums import WorkspaceRole

WRITE_ROLES = frozenset({WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor})
APPROVE_ROLES = frozenset({WorkspaceRole.owner, WorkspaceRole.admin})

REQUIRED_FIELDS = (
    "product_name",
    "short_description",
    "primary_audience",
    "primary_customer_problem",
    "main_value_proposition",
    "primary_conversion_goal",
    "primary_conversion_url",
    "main_market",
    "differentiation",
    "important_capabilities",
    "quarterly_objective",
    "restricted_topics",
    "restricted_claims",
    "restricted_language",
    "brand_voice_guidance",
)


def completeness(values: Mapping[str, Any]) -> tuple[int, list[str]]:
    optional_restriction_lists = {"restricted_topics", "restricted_claims", "restricted_language"}
    missing = [
        field
        for field in REQUIRED_FIELDS
        if field not in values
        or (not values.get(field) and field not in optional_restriction_lists)
    ]
    return round((len(REQUIRED_FIELDS) - len(missing)) * 100 / len(REQUIRED_FIELDS)), missing


def changed_fields(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> list[str]:
    return sorted(
        field for field in after if before is None or before.get(field) != after.get(field)
    )


def can_write(role: WorkspaceRole) -> bool:
    return role in WRITE_ROLES


def can_approve(role: WorkspaceRole) -> bool:
    return role in APPROVE_ROLES
