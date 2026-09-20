from app.modules.products.policies.product_profile_policy import (
    can_approve,
    can_write,
    completeness,
)
from app.modules.workspaces.enums import WorkspaceRole


def test_completeness_requires_core_product_context() -> None:
    score, missing = completeness({})

    assert score == 0
    assert "primary_audience" in missing
    assert "main_market" in missing
    assert "brand_voice_guidance" not in missing


def test_completeness_counts_explicit_empty_restrictions_as_complete() -> None:
    values = {
        "product_name": "Example",
        "short_description": "An example product",
        "primary_audience": "Small teams",
        "primary_customer_problem": "Marketing is inconsistent",
        "main_value_proposition": "Turn work into useful campaigns",
        "primary_conversion_goal": "signup",
        "primary_conversion_url": "https://example.com/signup",
        "main_market": "Global",
        "differentiation": "Evidence-first workflows",
        "important_capabilities": ["Planning"],
        "quarterly_objective": "Increase qualified signups",
        "restricted_topics": [],
        "restricted_claims": [],
        "restricted_language": [],
        "brand_voice_guidance": "Clear and direct",
    }

    assert completeness(values) == (100, [])


def test_profile_role_policies_are_explicit() -> None:
    assert can_write(WorkspaceRole.editor)
    assert not can_write(WorkspaceRole.reviewer)
    assert can_approve(WorkspaceRole.admin)
    assert not can_approve(WorkspaceRole.editor)
