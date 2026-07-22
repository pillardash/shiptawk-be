from typing import Protocol

from app.domains.generation.models import GenerationRequest, ProviderGenerationResult


class ProviderTransientError(Exception):
    """Retryable provider transport or availability failure."""


class ProviderPermanentError(Exception):
    """A non-retryable provider rejection with no provider content exposed."""


class GenerationProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def generate(self, request: GenerationRequest) -> ProviderGenerationResult: ...
