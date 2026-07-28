from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class SearchProviderCapabilities:
    supports_oauth: bool
    supports_property_discovery: bool
    request_page_limit: int
    maximum_rows_per_day: int
    finalization_lag_days: int
    reporting_timezone: str = "UTC"

    @property
    def maximum_row_limit(self) -> int:
        return self.request_page_limit


@dataclass(frozen=True, slots=True)
class SearchAuthorizationGrant:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: tuple[str, ...]
    token_type: str


@dataclass(frozen=True, slots=True)
class SearchProviderIdentity:
    subject: str
    email: str


@dataclass(frozen=True, slots=True)
class SearchProviderProperty:
    provider_property_id: str
    display_name: str
    property_type: str
    permission_level: str


@dataclass(frozen=True, slots=True)
class SearchPerformanceRequest:
    property_id: str
    start_date: date
    end_date: date
    dimensions: tuple[str, ...]
    start_row: int = 0
    row_limit: int = 25_000


@dataclass(frozen=True, slots=True)
class SearchPerformanceRow:
    date: date
    query: str | None
    page: str | None
    clicks: int
    impressions: int
    ctr: Decimal
    position: Decimal
    grain: Literal["site_total", "query_page"]

    def __post_init__(self) -> None:
        dimensions_present = self.query is not None and self.page is not None
        if dimensions_present != (self.grain == "query_page"):
            raise ValueError("performance row dimensions do not match grain")


@dataclass(frozen=True, slots=True)
class SearchPerformanceResult:
    rows: tuple[SearchPerformanceRow, ...]
    next_start_row: int | None
    response_row_count: int


class SearchProviderError(Exception):
    retryable = False

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SearchProviderTransientError(SearchProviderError):
    retryable = True


class SearchProviderRateLimitError(SearchProviderTransientError):
    pass


class SearchProviderAuthorizationError(SearchProviderError):
    pass


class SearchProviderInvalidResponseError(SearchProviderError):
    pass


class SearchProviderConfigurationError(SearchProviderError):
    pass


class SearchProvider(Protocol):
    provider_key: str
    capabilities: SearchProviderCapabilities

    def build_authorization_url(
        self, *, redirect_uri: str, state: str, code_challenge: str
    ) -> str: ...
    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> SearchAuthorizationGrant: ...
    async def refresh_authorization(
        self, grant: SearchAuthorizationGrant
    ) -> SearchAuthorizationGrant: ...
    async def revoke(self, grant: SearchAuthorizationGrant) -> None: ...
    async def get_identity(self, grant: SearchAuthorizationGrant) -> SearchProviderIdentity: ...
    async def list_properties(
        self, grant: SearchAuthorizationGrant
    ) -> tuple[SearchProviderProperty, ...]: ...
    async def query_performance(
        self, grant: SearchAuthorizationGrant, request: SearchPerformanceRequest
    ) -> SearchPerformanceResult: ...


__all__ = [name for name in globals() if name.startswith("Search")]
