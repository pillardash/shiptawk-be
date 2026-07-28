from collections.abc import Mapping

from app.modules.llm.providers.base_provider import LLMProvider
from app.modules.llm.providers.llm_exceptions import LLMPermanentError


class LLMProviderRegistry:
    def __init__(self, providers: Mapping[str, LLMProvider]) -> None:
        self._providers = dict(providers)

    def get(self, name: str) -> LLMProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise LLMPermanentError(f"llm_provider_not_configured:{name}") from exc
