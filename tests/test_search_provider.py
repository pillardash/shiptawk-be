from datetime import UTC, date, datetime

import pytest

from app.modules.search_intelligence.providers.search.fake_search_provider import FakeSearchProvider
from app.modules.search_intelligence.providers.search.search_provider import (
    SearchAuthorizationGrant,
    SearchPerformanceRequest,
    SearchPerformanceResult,
    SearchProviderConfigurationError,
    SearchProviderProperty,
)
from app.modules.search_intelligence.providers.search.search_registry_provider import (
    SearchProviderRegistry,
)


def _grant() -> SearchAuthorizationGrant:
    return SearchAuthorizationGrant(
        access_token="access-secret",
        refresh_token="refresh-secret",
        expires_at=datetime(2026, 7, 27, tzinfo=UTC),
        scopes=("scope",),
        token_type="Bearer",
    )


@pytest.mark.asyncio
async def test_fake_provider_is_deterministic_and_records_calls() -> None:
    request = SearchPerformanceRequest(
        property_id="sc-domain:example.com",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        dimensions=("date", "query", "page"),
        start_row=0,
        row_limit=100,
    )
    expected = SearchPerformanceResult(rows=(), next_start_row=None, response_row_count=0)
    provider = FakeSearchProvider(properties=(), performance_results={request: expected})
    assert await provider.list_properties(_grant()) == ()
    assert await provider.query_performance(_grant(), request) == expected
    assert provider.calls == [("list_properties", None), ("query_performance", request)]


def test_registry_requires_explicit_unique_provider_registration() -> None:
    provider = FakeSearchProvider(
        properties=(
            SearchProviderProperty(
                provider_property_id="sc-domain:example.com",
                display_name="example.com",
                property_type="domain",
                permission_level="siteOwner",
            ),
        )
    )
    registry = SearchProviderRegistry([provider])
    assert registry.get("fake_search") is provider
    with pytest.raises(SearchProviderConfigurationError, match="provider_not_registered"):
        registry.get("missing")
    with pytest.raises(SearchProviderConfigurationError, match="provider_already_registered"):
        registry.register(provider)
