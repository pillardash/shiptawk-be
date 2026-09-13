import ast
import importlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import cast

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from sqlalchemy import Table

from app.api.v1.health import router as health_router
from app.api.v1.router import router as v1_router
from app.db.base import Base
from app.modules.achievement_digests.api.router import router as achievement_digests_router
from app.modules.analytics.api.router import browser_event_router
from app.modules.analytics.api.router import router as analytics_router
from app.modules.drafts.api.router import router as drafts_router
from app.modules.evidence.api.router import router as evidence_router
from app.modules.identity.api import browser_router as browser_auth_router
from app.modules.identity.api import identity_router
from app.modules.integrations.api.router import router as integrations_router
from app.modules.integrations.api.webhook_router import router as webhook_router
from app.modules.notifications.api.router import router as notifications_router
from app.modules.operator.api.operator_lookup_router import router as operator_lookup_router
from app.modules.operator.api.operator_run_router import router as operator_run_router
from app.modules.operator.api.opportunity_router import router as opportunity_router
from app.modules.operator.api.weekly_growth_router import router as weekly_growth_router
from app.modules.products.api.activation_router import router as activation_router
from app.modules.products.api.product_router import product_router
from app.modules.search_intelligence.api.search_connection_router import (
    product_search_router,
    search_callback_router,
)
from app.modules.search_intelligence.api.search_read_router import (
    search_read_router,
    workspace_search_router,
)
from app.modules.search_intelligence.api.search_sync_router import search_sync_router

EXPECTED_TABLES = {
    "achievement_digest_feedback",
    "achievement_digests",
    "action_risk_reviews",
    "action_events",
    "approval_requests",
    "auth_sessions",
    "claim_evidence_links",
    "claims",
    "draft_feedback_events",
    "draft_status_events",
    "drafts",
    "event_evaluations",
    "evidence_excerpts",
    "evidence_items",
    "evidence_reviews",
    "execution_runs",
    "generation_runs",
    "github_raw_event_consumers",
    "github_raw_events",
    "integration_connections",
    "integration_oauth_transactions",
    "llm_execution_attempts",
    "llm_executions",
    "marketing_plans",
    "measurement_windows",
    "normalized_events",
    "oauth_identities",
    "oauth_transactions",
    "operator_runs",
    "operator_command_receipts",
    "operator_recommendation_decisions",
    "operator_run_snapshots",
    "operator_recommendation_evidence",
    "operator_recommendations",
    "outbox_events",
    "opportunities",
    "opportunity_evaluations",
    "opportunity_evidence",
    "opportunity_feedback",
    "opportunity_status_events",
    "plan_actions",
    "plan_views",
    "prepared_assets",
    "product_events",
    "recommendation_usefulness_feedback",
    "publication_attempts",
    "product_repositories",
    "products",
    "product_profiles",
    "product_profile_audit_events",
    "repo_changelog_digests",
    "repo_changelog_entries",
    "repos",
    "search_daily_metrics",
    "search_pages",
    "search_provider_properties",
    "search_queries",
    "search_sources",
    "search_sync_runs",
    "tweet_candidates",
    "users",
    "verification_runs",
    "website_capability_mappings",
    "website_crawl_results",
    "website_crawl_runs",
    "website_pages",
    "website_sources",
    "weekly_growth_deliveries",
    "workspace_memberships",
    "workspaces",
}

MODEL_PACKAGES = [
    "app.db.outbox",
    "app.modules.achievement_digests.models",
    "app.modules.analytics.models",
    "app.modules.drafts.models",
    "app.modules.evidence.models",
    "app.modules.llm.models",
    "app.modules.identity.models.oauth",
    "app.modules.identity.models.users",
    "app.modules.integrations.models",
    "app.modules.operator.models",
    "app.modules.products.models",
    "app.modules.repos.models",
    "app.modules.search_intelligence.models",
    "app.modules.workspaces.models",
]

OWNED_TABLES = {
    "app.db.outbox": {"outbox_events"},
    "app.modules.achievement_digests.models": {
        "achievement_digest_feedback",
        "achievement_digests",
    },
    "app.modules.drafts.models": {
        "draft_feedback_events",
        "draft_status_events",
        "drafts",
        "tweet_candidates",
    },
    "app.modules.llm.models": {
        "generation_runs",
        "llm_execution_attempts",
        "llm_executions",
    },
    "app.modules.operator.models": {
        "event_evaluations",
        "operator_recommendation_evidence",
    },
    "app.modules.integrations.models": {
        "github_raw_event_consumers",
        "github_raw_events",
        "integration_connections",
        "integration_oauth_transactions",
        "normalized_events",
    },
    "app.modules.products.models": {
        "product_profile_audit_events",
        "product_profiles",
        "product_repositories",
        "products",
        "website_capability_mappings",
        "website_crawl_results",
        "website_crawl_runs",
        "website_pages",
        "website_sources",
    },
    "app.modules.repos.models": {
        "repo_changelog_digests",
        "repo_changelog_entries",
        "repos",
    },
    "app.modules.search_intelligence.models": {
        "search_daily_metrics",
        "search_pages",
        "search_provider_properties",
        "search_queries",
        "search_sources",
        "search_sync_runs",
    },
}

OWNED_ROUTERS = [
    activation_router,
    identity_router,
    browser_auth_router,
    health_router,
    achievement_digests_router,
    analytics_router,
    browser_event_router,
    notifications_router,
    product_router,
    drafts_router,
    evidence_router,
    operator_run_router,
    operator_lookup_router,
    opportunity_router,
    weekly_growth_router,
    integrations_router,
    webhook_router,
    product_search_router,
    search_callback_router,
    search_sync_router,
    search_read_router,
    workspace_search_router,
]


def _route_keys(router: APIRouter) -> Counter[tuple[str, str, object]]:
    return Counter(
        (method, route.path, route.endpoint)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
        if method not in {"HEAD", "OPTIONS"}
    )


@pytest.mark.parametrize("model_packages", [MODEL_PACKAGES, list(reversed(MODEL_PACKAGES))])
def test_model_packages_register_complete_metadata_in_any_import_order(
    model_packages: list[str],
) -> None:
    script = (
        "import importlib, json; "
        "from app.db.base import Base; "
        f"packages = {model_packages!r}; "
        "[importlib.import_module(package) for package in packages]; "
        "print(json.dumps(sorted(Base.metadata.tables)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert set(json.loads(result.stdout)) == EXPECTED_TABLES


def test_owned_table_exports_are_canonical_metadata_objects() -> None:
    for module_name, table_names in OWNED_TABLES.items():
        module = importlib.import_module(module_name)
        for table_name in table_names:
            exported = getattr(module, table_name)
            assert exported is Base.metadata.tables[table_name]
            assert exported.metadata is Base.metadata


def test_v1_router_mounts_each_owned_route_exactly_once() -> None:
    expected: Counter[tuple[str, str, object]] = Counter()
    for owned_router in OWNED_ROUTERS:
        expected.update(_route_keys(owned_router))

    actual = _route_keys(v1_router)

    assert actual == expected
    assert all(count == 1 for count in actual.values())


def test_modules_use_responsibility_packages_instead_of_flat_layer_files() -> None:
    modules_root = Path(__file__).parents[1] / "app" / "modules"
    forbidden_names = {
        "models.py",
        "provider.py",
        "repository.py",
        "router.py",
        "schemas.py",
        "service.py",
    }
    flat_files = sorted(
        str(path.relative_to(modules_root))
        for path in modules_root.rglob("*.py")
        if path.name in forbidden_names and path.parent.parent == modules_root
    )

    assert flat_files == []


def test_operator_detector_implementations_have_detector_suffix() -> None:
    root = Path(__file__).parents[1] / "app" / "modules" / "operator" / "detectors"
    incorrectly_named = sorted(
        path.name
        for path in root.glob("*.py")
        if path.name != "__init__.py" and not path.name.endswith("_detector.py")
    )
    assert incorrectly_named == []


def test_products_module_uses_flat_responsibility_packages_and_identifiable_names() -> None:
    products_root = Path(__file__).parents[1] / "app" / "modules" / "products"
    responsibility_suffixes = {
        "api": "_router.py",
        "enums": "_enum.py",
        "policies": "_policy.py",
        "providers": "_provider.py",
        "repositories": "_repository.py",
        "schemas": "_schema.py",
        "services": "_service.py",
    }
    incorrectly_named = sorted(
        str(path.relative_to(products_root))
        for package, suffix in responsibility_suffixes.items()
        for path in (products_root / package).glob("*.py")
        if path.name != "__init__.py" and not path.name.endswith(suffix)
    )
    nested_domain_packages = sorted(
        str(path.relative_to(products_root))
        for path in products_root.iterdir()
        if path.is_dir()
        and path.name not in {*responsibility_suffixes, "models", "prompts", "__pycache__"}
    )

    assert incorrectly_named == []
    assert nested_domain_packages == []
    assert (products_root / "prompts" / "__init__.py").read_text() == ""


def test_product_model_names_and_files_match_singular_table_names() -> None:
    def singular(table_name: str) -> str:
        return table_name[:-3] + "y" if table_name.endswith("ies") else table_name.removesuffix("s")

    product_mappers = [
        mapper
        for mapper in Base.registry.mappers
        if mapper.class_.__module__.startswith("app.modules.products.models")
    ]

    for mapper in product_mappers:
        expected_stem = singular(cast(Table, mapper.local_table).name)
        expected_class = "".join(part.capitalize() for part in expected_stem.split("_"))
        assert mapper.class_.__name__ == expected_class
        assert mapper.class_.__module__.rsplit(".", 1)[-1] == expected_stem


def test_module_initializers_do_not_register_runtime_compatibility_aliases() -> None:
    modules_root = Path(__file__).parents[1] / "app" / "modules"
    forbidden_initializers = sorted(
        str(path.relative_to(modules_root))
        for path in modules_root.rglob("__init__.py")
        if "sys.modules" in path.read_text() or "ModuleType" in path.read_text()
    )

    assert forbidden_initializers == []


def test_integrations_has_no_flat_compatibility_modules() -> None:
    integrations_root = Path(__file__).parents[1] / "app" / "modules" / "integrations"

    assert not (integrations_root / "event_normalizer.py").exists()
    assert not (integrations_root / "x_oauth.py").exists()


def test_repositories_and_api_routers_do_not_own_transactions() -> None:
    modules_root = Path(__file__).parents[1] / "app" / "modules"
    violations: list[str] = []
    for layer in ("repositories", "api"):
        for path in modules_root.glob(f"*/{layer}/**/*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"commit", "rollback"}
                ):
                    relative = path.relative_to(modules_root)
                    violations.append(f"{relative}:{node.lineno}:{node.func.attr}")

    assert violations == []
