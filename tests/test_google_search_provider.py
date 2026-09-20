from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.modules.search_intelligence.providers.search.google_search_provider import (
    GoogleSearchProvider,
)
from app.modules.search_intelligence.providers.search.search_provider import (
    SearchAuthorizationGrant,
    SearchPerformanceRequest,
    SearchProviderAuthorizationError,
    SearchProviderConfigurationError,
    SearchProviderError,
    SearchProviderInvalidResponseError,
    SearchProviderRateLimitError,
    SearchProviderTransientError,
)


def _grant() -> SearchAuthorizationGrant:
    return SearchAuthorizationGrant(
        access_token="access-secret",
        refresh_token="refresh-secret",
        expires_at=datetime(2026, 7, 27, tzinfo=UTC),
        scopes=("openid", "email"),
        token_type="Bearer",
    )


def _provider(transport: httpx.MockTransport) -> tuple[GoogleSearchProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=transport)
    return GoogleSearchProvider(
        client_id="client-id",
        client_secret="client-secret",
        http_client=client,
        now=lambda: datetime(2026, 7, 27, 12, tzinfo=UTC),
    ), client


def test_authorization_url_has_state_pkce_and_required_offline_scopes() -> None:
    provider, _ = _provider(httpx.MockTransport(lambda request: httpx.Response(500)))
    query = parse_qs(
        urlsplit(
            provider.build_authorization_url(
                redirect_uri="https://app.example.com/oauth/callback",
                state="opaque-state",
                code_challenge="challenge",
            )
        ).query
    )
    assert query == {
        "access_type": ["offline"],
        "client_id": ["client-id"],
        "code_challenge": ["challenge"],
        "code_challenge_method": ["S256"],
        "prompt": ["consent"],
        "redirect_uri": ["https://app.example.com/oauth/callback"],
        "response_type": ["code"],
        "scope": ["openid email https://www.googleapis.com/auth/webmasters.readonly"],
        "state": ["opaque-state"],
    }


@pytest.mark.asyncio
async def test_exchange_and_refresh_send_exact_forms_and_preserve_refresh_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = {
            "access_token": "new-access" if len(requests) == 1 else "refreshed-access",
            "expires_in": 3600 if len(requests) == 1 else 1800,
            "token_type": "Bearer",
        }
        if len(requests) == 1:
            payload["refresh_token"] = "new-refresh"
            payload["scope"] = "openid email"
        return httpx.Response(200, json=payload)

    provider, client = _provider(httpx.MockTransport(handler))
    exchanged = await provider.exchange_code(
        code="authorization-code",
        code_verifier="verifier",
        redirect_uri="https://app.example.com/oauth/callback",
    )
    refreshed = await provider.refresh_authorization(exchanged)
    await client.aclose()
    assert parse_qs(requests[0].content.decode()) == {
        "client_id": ["client-id"],
        "client_secret": ["client-secret"],
        "code": ["authorization-code"],
        "code_verifier": ["verifier"],
        "grant_type": ["authorization_code"],
        "redirect_uri": ["https://app.example.com/oauth/callback"],
    }
    assert parse_qs(requests[1].content.decode()) == {
        "client_id": ["client-id"],
        "client_secret": ["client-secret"],
        "grant_type": ["refresh_token"],
        "refresh_token": ["new-refresh"],
    }
    assert refreshed.refresh_token == "new-refresh"
    assert refreshed.scopes == ("openid", "email")
    assert refreshed.expires_at == datetime(2026, 7, 27, 12, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_exchange_normalizes_google_email_scope_alias_and_preserves_missing_scope() -> None:
    provider, client = _provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "openid https://www.googleapis.com/auth/userinfo.email "
                    "https://www.googleapis.com/auth/webmasters.readonly",
                },
            )
        )
    )

    exchanged = await provider.exchange_code(
        code="authorization-code",
        code_verifier="verifier",
        redirect_uri="https://app.example.com/oauth/callback",
    )

    await client.aclose()
    assert exchanged.scopes == (
        "openid",
        "email",
        "https://www.googleapis.com/auth/webmasters.readonly",
    )


@pytest.mark.asyncio
async def test_identity_discovery_revocation_and_performance_requests_are_exact() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/userinfo":
            return httpx.Response(200, json={"sub": "123", "email": "owner@example.com"})
        if request.url.path == "/webmasters/v3/sites":
            return httpx.Response(
                200,
                json={
                    "siteEntry": [
                        {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"}
                    ]
                },
            )
        if request.url.path.endswith("/searchAnalytics/query"):
            return httpx.Response(
                200,
                json={
                    "rows": [
                        {
                            "keys": ["2026-07-01", "widgets", "https://example.com/widgets"],
                            "clicks": 2,
                            "impressions": 100,
                            "ctr": 0.02,
                            "position": 4.5,
                        }
                    ],
                    "responseAggregationType": "byPage",
                },
            )
        return httpx.Response(200)

    provider, client = _provider(httpx.MockTransport(handler))
    identity = await provider.get_identity(_grant())
    properties = await provider.list_properties(_grant())
    performance_request = SearchPerformanceRequest(
        property_id="sc-domain:example.com",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
        dimensions=("date", "query", "page"),
        start_row=25,
        row_limit=1,
    )
    result = await provider.query_performance(_grant(), performance_request)
    await provider.revoke(_grant())
    await client.aclose()
    assert (identity.subject, identity.email) == ("123", "owner@example.com")
    assert properties[0].property_type == "domain"
    assert str(requests[0].url) == "https://openidconnect.googleapis.com/v1/userinfo"
    assert requests[0].headers["authorization"] == "Bearer access-secret"
    assert requests[2].url.raw_path == (
        b"/webmasters/v3/sites/sc-domain%3Aexample.com/searchAnalytics/query"
    )
    assert requests[2].read().decode() == (
        '{"startDate":"2026-07-01","endDate":"2026-07-01",'
        '"dimensions":["date","query","page"],"startRow":25,"rowLimit":1,'
        '"dataState":"final"}'
    )
    assert (result.response_row_count, result.next_start_row) == (1, 26)
    assert result.rows[0].query == "widgets"
    assert parse_qs(requests[3].content.decode()) == {"token": ["refresh-secret"]}


@pytest.mark.asyncio
async def test_performance_supports_site_totals_without_anonymized_query_loss() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.read().decode() == (
            '{"startDate":"2026-07-01","endDate":"2026-07-01",'
            '"dimensions":["date"],"startRow":0,"rowLimit":25000,"dataState":"final"}'
        )
        return httpx.Response(
            200,
            json={
                "rows": [
                    {
                        "keys": ["2026-07-01"],
                        "clicks": 7,
                        "impressions": 90,
                        "ctr": 7 / 90,
                        "position": 3.25,
                    }
                ]
            },
        )

    provider, client = _provider(httpx.MockTransport(handler))
    result = await provider.query_performance(
        _grant(),
        SearchPerformanceRequest(
            property_id="sc-domain:example.com",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
            dimensions=("date",),
        ),
    )
    await client.aclose()

    assert result.rows[0].query is None
    assert result.rows[0].page is None
    assert result.rows[0].grain == "site_total"
    assert provider.capabilities.finalization_lag_days == 3
    assert provider.capabilities.maximum_rows_per_day == 50_000
    assert provider.capabilities.request_page_limit == 25_000


@pytest.mark.asyncio
async def test_performance_rejects_key_count_for_requested_grain() -> None:
    provider, client = _provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "rows": [
                        {
                            "keys": ["2026-07-01", "unexpected"],
                            "clicks": 0,
                            "impressions": 0,
                            "ctr": 0,
                            "position": 0,
                        }
                    ]
                },
            )
        )
    )
    with pytest.raises(SearchProviderInvalidResponseError):
        await provider.query_performance(
            _grant(),
            SearchPerformanceRequest(
                property_id="sc-domain:example.com",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 1),
                dimensions=("date",),
            ),
        )
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error",
    [
        (401, SearchProviderAuthorizationError),
        (429, SearchProviderRateLimitError),
        (503, SearchProviderTransientError),
    ],
)
async def test_http_errors_are_categorized_and_sanitized(
    status: int, error: type[Exception]
) -> None:
    secret = "provider-body-secret"
    provider, client = _provider(
        httpx.MockTransport(lambda request: httpx.Response(status, text=secret))
    )
    with pytest.raises(error) as caught:
        await provider.get_identity(_grant())
    await client.aclose()
    assert secret not in str(caught.value)
    assert "access-secret" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload", "operation", "expected", "code"),
    [
        (
            400,
            {"error": "invalid_grant", "error_description": "provider-body-secret"},
            "refresh",
            SearchProviderAuthorizationError,
            "google_authorization_failed",
        ),
        (
            403,
            {
                "error": {
                    "message": "provider-body-secret",
                    "errors": [{"reason": "quotaExceeded"}],
                }
            },
            "identity",
            SearchProviderRateLimitError,
            "google_rate_limited",
        ),
        (
            403,
            {
                "error": {
                    "status": "PERMISSION_DENIED",
                    "details": [{"reason": "SERVICE_DISABLED"}],
                }
            },
            "identity",
            SearchProviderConfigurationError,
            "google_search_api_not_enabled",
        ),
    ],
)
async def test_invalid_grant_and_quota_errors_map_to_sanitized_domain_errors(
    status: int,
    payload: dict[str, object],
    operation: str,
    expected: type[Exception],
    code: str,
) -> None:
    provider, client = _provider(
        httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    )
    with pytest.raises(expected, match=code) as caught:
        if operation == "refresh":
            await provider.refresh_authorization(_grant())
        else:
            await provider.get_identity(_grant())
    await client.aclose()
    assert "provider-body-secret" not in str(caught.value)
    assert "access-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_property_discovery_rejection_is_operation_specific_and_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "provider-body-secret"
    provider, client = _provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                headers={"x-request-id": "google-request-1"},
                json={"error": {"message": secret, "status": "INVALID_ARGUMENT"}},
            )
        )
    )

    with pytest.raises(SearchProviderError, match="google_property_discovery_rejected"):
        await provider.list_properties(_grant())

    await client.aclose()
    assert "operation=list_properties" in caplog.text
    assert "status=400" in caplog.text
    assert "provider_status=INVALID_ARGUMENT" in caplog.text
    assert "provider_request_id=google-request-1" in caplog.text
    assert secret not in caplog.text
    assert "access-secret" not in caplog.text


@pytest.mark.asyncio
async def test_malformed_success_is_sanitized() -> None:
    secret = "malformed-secret"
    provider, client = _provider(
        httpx.MockTransport(lambda request: httpx.Response(200, json={"sub": secret}))
    )
    with pytest.raises(
        SearchProviderInvalidResponseError, match="invalid_identity_response"
    ) as caught:
        await provider.get_identity(_grant())
    await client.aclose()
    assert secret not in str(caught.value)
