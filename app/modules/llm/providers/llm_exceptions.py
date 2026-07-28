class LLMProviderError(Exception):
    """Base error for a provider request that did not complete successfully."""


class LLMTransientError(LLMProviderError):
    """A provider or transport failure that may be retried by the caller."""


class LLMPermanentError(LLMProviderError):
    """A non-retryable provider or configuration failure."""


class LLMInvalidResponseError(LLMProviderError):
    """A provider returned an unreadable or unsupported response."""
