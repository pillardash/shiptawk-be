from app.domains.generation.models import GenerationRequest, ProviderGenerationResult
from app.domains.generation.provider import ProviderPermanentError, ProviderTransientError


class DeterministicGenerationProvider:
    def __init__(
        self,
        *,
        result: ProviderGenerationResult | None = None,
        error: ProviderTransientError | ProviderPermanentError | None = None,
    ) -> None:
        if (result is None) == (error is None):
            raise ValueError("Exactly one of result or error is required.")
        self._result = result
        self._error = error
        self.requests: list[GenerationRequest] = []

    @property
    def name(self) -> str:
        return "deterministic"

    @property
    def call_count(self) -> int:
        return len(self.requests)

    async def generate(self, request: GenerationRequest) -> ProviderGenerationResult:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result
