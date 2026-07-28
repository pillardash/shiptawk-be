from collections.abc import Iterable

from app.modules.search_intelligence.providers.search.search_provider import (
    SearchProvider,
    SearchProviderConfigurationError,
)


class SearchProviderRegistry:
    def __init__(self, providers: Iterable[SearchProvider] = ()) -> None:
        self._providers: dict[str, SearchProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: SearchProvider) -> None:
        if provider.provider_key in self._providers:
            raise SearchProviderConfigurationError("provider_already_registered")
        self._providers[provider.provider_key] = provider

    def get(self, provider_key: str) -> SearchProvider:
        try:
            return self._providers[provider_key]
        except KeyError as exc:
            raise SearchProviderConfigurationError("provider_not_registered") from exc


__all__ = ["SearchProviderRegistry"]
