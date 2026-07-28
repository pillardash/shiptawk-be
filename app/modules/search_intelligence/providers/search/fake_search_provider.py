from collections.abc import Mapping

from app.modules.search_intelligence.providers.search.search_provider import (
    SearchAuthorizationGrant,
    SearchPerformanceRequest,
    SearchPerformanceResult,
    SearchProviderCapabilities,
    SearchProviderConfigurationError,
    SearchProviderIdentity,
    SearchProviderProperty,
)


class FakeSearchProvider:
    provider_key = "fake_search"
    capabilities = SearchProviderCapabilities(True, True, 25_000, 50_000, 3)

    def __init__(
        self,
        *,
        properties: tuple[SearchProviderProperty, ...] = (),
        performance_results: Mapping[SearchPerformanceRequest, SearchPerformanceResult]
        | None = None,
        identity: SearchProviderIdentity | None = None,
    ) -> None:
        self._properties = properties
        self._performance_results = dict(performance_results or {})
        self._identity = identity or SearchProviderIdentity("fake-subject", "fake@example.com")
        self.calls: list[tuple[str, object | None]] = []

    def build_authorization_url(self, *, redirect_uri: str, state: str, code_challenge: str) -> str:
        self.calls.append(("build_authorization_url", (redirect_uri, state, code_challenge)))
        return f"https://fake.invalid/authorize?state={state}"

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> SearchAuthorizationGrant:
        self.calls.append(("exchange_code", (code, code_verifier, redirect_uri)))
        return SearchAuthorizationGrant("fake-access", "fake-refresh", None, (), "Bearer")

    async def refresh_authorization(
        self, grant: SearchAuthorizationGrant
    ) -> SearchAuthorizationGrant:
        self.calls.append(("refresh_authorization", grant))
        return grant

    async def revoke(self, grant: SearchAuthorizationGrant) -> None:
        self.calls.append(("revoke", grant))

    async def get_identity(self, grant: SearchAuthorizationGrant) -> SearchProviderIdentity:
        self.calls.append(("get_identity", None))
        return self._identity

    async def list_properties(
        self, grant: SearchAuthorizationGrant
    ) -> tuple[SearchProviderProperty, ...]:
        self.calls.append(("list_properties", None))
        return self._properties

    async def query_performance(
        self,
        grant: SearchAuthorizationGrant,
        request: SearchPerformanceRequest,
    ) -> SearchPerformanceResult:
        self.calls.append(("query_performance", request))
        try:
            return self._performance_results[request]
        except KeyError as exc:
            raise SearchProviderConfigurationError("fake_response_not_configured") from exc


__all__ = ["FakeSearchProvider"]
