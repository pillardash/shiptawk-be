from typing import cast

from sqlalchemy import (
    CheckConstraint,
    Constraint,
    ForeignKeyConstraint,
    Index,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects import sqlite

from app.modules.products.models import (
    WebsiteCapabilityMapping,
    WebsiteCrawlResult,
    WebsiteCrawlRun,
    WebsitePage,
    WebsiteSource,
)


def _constraint_names(model: type[object], kind: type[Constraint]) -> set[str | None]:
    return {
        str(constraint.name) if constraint.name is not None else None
        for constraint in cast(Table, model.__table__).constraints  # type: ignore[attr-defined]
        if isinstance(constraint, kind)
    }


def test_website_models_define_lifecycle_and_idempotency_constraints() -> None:
    assert "ck_website_sources_source_status_valid" in _constraint_names(
        WebsiteSource, CheckConstraint
    )
    assert "ck_website_crawl_runs_crawl_lifecycle_valid" in _constraint_names(
        WebsiteCrawlRun, CheckConstraint
    )
    assert "ck_website_crawl_results_result_status_valid" in _constraint_names(
        WebsiteCrawlResult, CheckConstraint
    )
    assert "ck_website_capability_mappings_coverage_status_valid" in _constraint_names(
        WebsiteCapabilityMapping, CheckConstraint
    )

    assert "uq_website_sources_product" in _constraint_names(WebsiteSource, UniqueConstraint)
    assert "uq_website_crawl_runs_idempotency" in _constraint_names(
        WebsiteCrawlRun, UniqueConstraint
    )
    assert "uq_website_pages_canonical_url" in _constraint_names(WebsitePage, UniqueConstraint)
    assert "uq_website_crawl_results_run_page" in _constraint_names(
        WebsiteCrawlResult, UniqueConstraint
    )
    assert "uq_website_capability_mappings_run_capability" in _constraint_names(
        WebsiteCapabilityMapping, UniqueConstraint
    )


def test_only_one_crawl_can_be_active_for_a_source() -> None:
    active_index = next(
        index
        for index in cast(Table, WebsiteCrawlRun.__table__).indexes
        if isinstance(index, Index) and index.name == "uq_website_crawl_runs_active_source"
    )

    assert active_index.unique
    assert str(active_index.dialect_options["postgresql"]["where"]) == (
        "status IN ('pending', 'running')"
    )
    assert str(active_index.dialect_options["sqlite"]["where"]) == (
        "status IN ('pending', 'running')"
    )


def test_website_child_references_are_product_bound_composite_foreign_keys() -> None:
    expected = {
        WebsiteCrawlRun: {"product_id", "source_id"},
        WebsitePage: {"product_id", "source_id", "first_discovered_run_id"},
        WebsiteCrawlResult: {"product_id", "source_id", "crawl_run_id", "page_id"},
        WebsiteCapabilityMapping: {"product_id", "source_id", "crawl_run_id", "page_id"},
    }

    for model, child_ids in expected.items():
        constraints = [
            constraint
            for constraint in cast(Table, model.__table__).constraints
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
        assert all("product_id" in {column.name for column in item.columns} for item in constraints)


def test_website_json_columns_compile_for_sqlite() -> None:
    json_columns = (
        WebsiteSource.__table__.c.sitemap_urls,
        WebsiteCrawlRun.__table__.c.summary,
        WebsiteCrawlResult.__table__.c.headings,
        WebsiteCrawlResult.__table__.c.indexability_reasons,
        WebsiteCapabilityMapping.__table__.c.evidence,
    )

    for column in json_columns:
        assert column.type.dialect_impl(sqlite.dialect()).__class__.__name__ == "_SQliteJson"
