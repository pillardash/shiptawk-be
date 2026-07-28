from typing import cast

from sqlalchemy import (
    CheckConstraint,
    Constraint,
    ForeignKeyConstraint,
    Index,
    Table,
    UniqueConstraint,
)

from app.modules.search_intelligence.models import (
    SearchDailyMetric,
    SearchPage,
    SearchProviderProperty,
    SearchQuery,
    SearchSource,
    SearchSyncRun,
)


def _names(model: type[object], kind: type[Constraint]) -> set[str | None]:
    return {
        str(item.name) if item.name is not None else None
        for item in cast(Table, model.__table__).constraints  # type: ignore[attr-defined]
        if isinstance(item, kind)
    }


def _index(model: type[object], name: str) -> Index:
    return next(item for item in cast(Table, model.__table__).indexes if item.name == name)  # type: ignore[attr-defined]


def test_search_models_define_identity_lifecycle_and_grain_constraints() -> None:
    assert "uq_search_provider_properties_connection_property" in _names(
        SearchProviderProperty, UniqueConstraint
    )
    assert "uq_search_sync_runs_source_idempotency" in _names(SearchSyncRun, UniqueConstraint)
    assert "uq_search_queries_source_fingerprint" in _names(SearchQuery, UniqueConstraint)
    assert "uq_search_pages_source_fingerprint" in _names(SearchPage, UniqueConstraint)
    assert "ck_search_daily_metrics_grain_dimensions" in _names(SearchDailyMetric, CheckConstraint)
    assert "ck_search_daily_metrics_counts_nonnegative" in _names(
        SearchDailyMetric, CheckConstraint
    )


def test_search_partial_uniqueness_is_postgres_and_sqlite_compatible() -> None:
    source = _index(SearchSource, "uq_search_sources_active_product_provider")
    run = _index(SearchSyncRun, "uq_search_sync_runs_active_source")
    site = _index(SearchDailyMetric, "uq_search_daily_metrics_site_total")
    query_page = _index(SearchDailyMetric, "uq_search_daily_metrics_query_page")
    assert (
        source.unique and str(source.dialect_options["postgresql"]["where"]) == "status = 'active'"
    )
    assert str(source.dialect_options["sqlite"]["where"]) == "status = 'active'"
    assert run.unique and "pending" in str(run.dialect_options["postgresql"]["where"])
    assert site.unique and str(site.dialect_options["sqlite"]["where"]) == "grain = 'site_total'"
    assert query_page.unique and "query_page" in str(
        query_page.dialect_options["postgresql"]["where"]
    )


def test_search_children_use_composite_tenant_foreign_keys() -> None:
    expected = {
        SearchProviderProperty: {"fk_search_provider_properties_connection_tenant"},
        SearchSource: {
            "fk_search_sources_product_tenant",
            "fk_search_sources_connection_tenant",
            "fk_search_sources_property_tenant",
            "fk_search_sources_website_source_tenant",
        },
        SearchSyncRun: {"fk_search_sync_runs_source_tenant"},
        SearchQuery: {"fk_search_queries_source_tenant"},
        SearchPage: {"fk_search_pages_source_tenant", "fk_search_pages_website_page_tenant"},
        SearchDailyMetric: {
            "fk_search_daily_metrics_source_tenant",
            "fk_search_daily_metrics_query_tenant",
            "fk_search_daily_metrics_page_tenant",
        },
    }
    for model, names in expected.items():
        actual = {
            str(item.name)
            for item in cast(Table, model.__table__).constraints
            if isinstance(item, ForeignKeyConstraint) and len(item.columns) > 1
        }
        assert names <= actual
