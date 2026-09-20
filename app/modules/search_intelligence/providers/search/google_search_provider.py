import logging
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from urllib.parse import quote, urlencode

import httpx

from app.modules.search_intelligence.providers.search.search_provider import (
    SearchAuthorizationGrant,
    SearchPerformanceRequest,
    SearchPerformanceResult,
    SearchPerformanceRow,
    SearchProviderAuthorizationError,
    SearchProviderCapabilities,
    SearchProviderConfigurationError,
    SearchProviderError,
    SearchProviderIdentity,
    SearchProviderInvalidResponseError,
    SearchProviderProperty,
    SearchProviderRateLimitError,
    SearchProviderTransientError,
)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
SITES_ENDPOINT = "https://www.googleapis.com/webmasters/v3/sites"
SCOPES = ("openid", "email", "https://www.googleapis.com/auth/webmasters.readonly")
SCOPE_ALIASES = {"https://www.googleapis.com/auth/userinfo.email": "email"}
logger = logging.getLogger(__name__)


def normalize_scopes(scopes: object) -> tuple[str, ...]:
    if not isinstance(scopes, str):
        return ()
    return tuple(dict.fromkeys(SCOPE_ALIASES.get(scope, scope) for scope in scopes.split()))


def _operation(url: str) -> str:
    return {
        TOKEN_ENDPOINT: "token_exchange",
        USERINFO_ENDPOINT: "userinfo",
        SITES_ENDPOINT: "list_properties",
    }.get(url, "search_performance")


def _error_details(response: httpx.Response) -> tuple[str | None, tuple[str, ...]]:
    try:
        payload = response.json()
    except ValueError:
        return None, ()
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return None, ()
    error = payload["error"]
    status = error.get("status") if isinstance(error.get("status"), str) else None
    reasons: list[str] = []
    legacy = error.get("errors", [])
    if isinstance(legacy, list):
        reasons.extend(
            reason
            for item in legacy
            if isinstance(item, dict) and isinstance((reason := item.get("reason")), str)
        )
    details = error.get("details", [])
    if isinstance(details, list):
        reasons.extend(
            reason
            for item in details
            if isinstance(item, dict) and isinstance((reason := item.get("reason")), str)
        )
    return status, tuple(dict.fromkeys(reasons))


class GoogleSearchProvider:
    provider_key = "google_search"
    capabilities = SearchProviderCapabilities(True, True, 25_000, 50_000, 3, "America/Los_Angeles")

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        http_client: httpx.AsyncClient,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not client_id or not client_secret:
            raise SearchProviderConfigurationError("google_credentials_missing")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http_client = http_client
        self._now = now

    def build_authorization_url(self, *, redirect_uri: str, state: str, code_challenge: str) -> str:
        return f"{AUTHORIZATION_ENDPOINT}?{
            urlencode(
                {
                    'client_id': self._client_id,
                    'redirect_uri': redirect_uri,
                    'response_type': 'code',
                    'scope': ' '.join(SCOPES),
                    'state': state,
                    'code_challenge': code_challenge,
                    'code_challenge_method': 'S256',
                    'access_type': 'offline',
                    'prompt': 'consent',
                }
            )
        }"

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        json: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._http_client.request(
                method,
                url,
                headers=headers,
                data=data,
                json=json,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise SearchProviderTransientError("google_transport_failure") from exc
        operation = _operation(url)
        provider_status, reasons = _error_details(response)
        if response.is_error:
            logger.warning(
                "Google Search request failed operation=%s status=%s provider_status=%s "
                "reasons=%s provider_request_id=%s",
                operation,
                response.status_code,
                provider_status,
                list(reasons),
                response.headers.get("x-request-id")
                or response.headers.get("x-guploader-uploadid"),
            )
        if response.status_code == 400 and url == TOKEN_ENDPOINT:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict) and payload.get("error") == "invalid_grant":
                raise SearchProviderAuthorizationError("google_authorization_failed")
        if response.status_code == 403:
            reason_set = set(reasons)
            if reason_set & {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}:
                raise SearchProviderRateLimitError("google_rate_limited")
            if reason_set & {"accessNotConfigured", "SERVICE_DISABLED", "API_DISABLED"}:
                raise SearchProviderConfigurationError("google_search_api_not_enabled")
            if reason_set & {"insufficientPermissions", "ACCESS_DENIED"}:
                raise SearchProviderAuthorizationError("google_search_property_access_denied")
            raise SearchProviderAuthorizationError("google_authorization_failed")
        if response.status_code == 401:
            raise SearchProviderAuthorizationError("google_authorization_failed")
        if response.status_code == 429:
            raise SearchProviderRateLimitError("google_rate_limited")
        if response.status_code >= 500:
            raise SearchProviderTransientError("google_service_unavailable")
        if response.is_error:
            code = {
                "token_exchange": "google_token_exchange_rejected",
                "userinfo": "google_identity_request_rejected",
                "list_properties": "google_property_discovery_rejected",
            }.get(operation, "google_search_request_rejected")
            raise SearchProviderError(code)
        return response

    @staticmethod
    def _json_object(response: httpx.Response, code: str) -> Mapping[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchProviderInvalidResponseError(code) from exc
        if not isinstance(payload, dict):
            raise SearchProviderInvalidResponseError(code)
        return cast(Mapping[str, Any], payload)

    async def _token(
        self,
        data: Mapping[str, str],
        preserved_refresh: str | None,
        preserved_scopes: tuple[str, ...] = (),
    ) -> SearchAuthorizationGrant:
        response = await self._request("POST", TOKEN_ENDPOINT, data=data)
        payload = self._json_object(response, "invalid_token_response")
        access_token = payload.get("access_token")
        token_type = payload.get("token_type")
        expires_in = payload.get("expires_in")
        scope = payload.get("scope")
        refresh = payload.get("refresh_token", preserved_refresh)
        if (
            not isinstance(access_token, str)
            or not isinstance(token_type, str)
            or not isinstance(expires_in, int)
            or expires_in <= 0
            or (scope is not None and not isinstance(scope, str))
            or (refresh is not None and not isinstance(refresh, str))
        ):
            raise SearchProviderInvalidResponseError("invalid_token_response")
        return SearchAuthorizationGrant(
            access_token,
            refresh,
            self._now() + timedelta(seconds=expires_in),
            normalize_scopes(scope)
            if isinstance(scope, str)
            else normalize_scopes(" ".join(preserved_scopes)),
            token_type,
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> SearchAuthorizationGrant:
        return await self._token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            None,
            SCOPES,
        )

    async def refresh_authorization(
        self, grant: SearchAuthorizationGrant
    ) -> SearchAuthorizationGrant:
        if grant.refresh_token is None:
            raise SearchProviderAuthorizationError("refresh_token_missing")
        return await self._token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": grant.refresh_token,
            },
            grant.refresh_token,
            grant.scopes,
        )

    async def revoke(self, grant: SearchAuthorizationGrant) -> None:
        token = grant.refresh_token or grant.access_token
        await self._request("POST", REVOCATION_ENDPOINT, data={"token": token})

    @staticmethod
    def _headers(grant: SearchAuthorizationGrant) -> dict[str, str]:
        return {"Authorization": f"Bearer {grant.access_token}"}

    async def get_identity(self, grant: SearchAuthorizationGrant) -> SearchProviderIdentity:
        response = await self._request("GET", USERINFO_ENDPOINT, headers=self._headers(grant))
        payload = self._json_object(response, "invalid_identity_response")
        subject, email = payload.get("sub"), payload.get("email")
        if not isinstance(subject, str) or not isinstance(email, str):
            raise SearchProviderInvalidResponseError("invalid_identity_response")
        return SearchProviderIdentity(subject, email)

    async def list_properties(
        self, grant: SearchAuthorizationGrant
    ) -> tuple[SearchProviderProperty, ...]:
        response = await self._request("GET", SITES_ENDPOINT, headers=self._headers(grant))
        payload = self._json_object(response, "invalid_properties_response")
        entries = payload.get("siteEntry", [])
        if not isinstance(entries, list):
            raise SearchProviderInvalidResponseError("invalid_properties_response")
        properties: list[SearchProviderProperty] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise SearchProviderInvalidResponseError("invalid_properties_response")
            property_id, permission = entry.get("siteUrl"), entry.get("permissionLevel")
            if not isinstance(property_id, str) or not isinstance(permission, str):
                raise SearchProviderInvalidResponseError("invalid_properties_response")
            domain = property_id.removeprefix("sc-domain:")
            properties.append(
                SearchProviderProperty(
                    property_id,
                    domain if property_id.startswith("sc-domain:") else property_id,
                    "domain" if property_id.startswith("sc-domain:") else "url_prefix",
                    permission,
                )
            )
        return tuple(properties)

    async def query_performance(
        self,
        grant: SearchAuthorizationGrant,
        request: SearchPerformanceRequest,
    ) -> SearchPerformanceResult:
        if (
            request.start_row < 0
            or not 1 <= request.row_limit <= self.capabilities.request_page_limit
        ):
            raise SearchProviderConfigurationError("invalid_performance_pagination")
        if request.dimensions not in {("date",), ("date", "query", "page")}:
            raise SearchProviderConfigurationError("unsupported_performance_dimensions")
        url = f"{SITES_ENDPOINT}/{quote(request.property_id, safe='')}/searchAnalytics/query"
        response = await self._request(
            "POST",
            url,
            headers=self._headers(grant),
            json={
                "startDate": request.start_date.isoformat(),
                "endDate": request.end_date.isoformat(),
                "dimensions": list(request.dimensions),
                "startRow": request.start_row,
                "rowLimit": request.row_limit,
                "dataState": "final",
            },
        )
        payload = self._json_object(response, "invalid_performance_response")
        raw_rows = payload.get("rows", [])
        if not isinstance(raw_rows, list):
            raise SearchProviderInvalidResponseError("invalid_performance_response")
        rows = tuple(self._parse_row(row, request.dimensions) for row in raw_rows)
        next_row = request.start_row + len(rows) if len(rows) == request.row_limit else None
        return SearchPerformanceResult(rows, next_row, len(rows))

    @staticmethod
    def _parse_row(raw: object, dimensions: tuple[str, ...]) -> SearchPerformanceRow:
        try:
            if not isinstance(raw, dict) or not isinstance(raw.get("keys"), list):
                raise ValueError
            keys = raw["keys"]
            if len(keys) != len(dimensions) or not all(isinstance(value, str) for value in keys):
                raise ValueError
            clicks, impressions = raw["clicks"], raw["impressions"]
            if not isinstance(clicks, int | float) or not isinstance(impressions, int | float):
                raise ValueError
            if (
                clicks < 0
                or impressions < 0
                or int(clicks) != clicks
                or int(impressions) != impressions
            ):
                raise ValueError
            return SearchPerformanceRow(
                date.fromisoformat(keys[0]),
                keys[1] if len(keys) == 3 else None,
                keys[2] if len(keys) == 3 else None,
                int(clicks),
                int(impressions),
                Decimal(str(raw["ctr"])),
                Decimal(str(raw["position"])),
                "query_page" if len(keys) == 3 else "site_total",
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise SearchProviderInvalidResponseError("invalid_performance_response") from exc


__all__ = ["GoogleSearchProvider"]
