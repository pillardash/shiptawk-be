from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.outbox import OutboxEvent
from app.modules.identity.models.users import User
from app.modules.integrations.models import IntegrationConnection
from app.modules.integrations.providers.credential_vault_provider import (
    DeterministicFakeIntegrationCredentialVault,
)
from app.modules.products.models import (
    Product,
    WebsiteCrawlResult,
    WebsiteCrawlRun,
    WebsitePage,
    WebsiteSource,
)
from app.modules.search_intelligence.models import (
    SearchDailyMetric,
    SearchPage,
    SearchProviderProperty,
    SearchQuery,
    SearchSource,
    SearchSyncRun,
)
from app.modules.search_intelligence.providers.search.fake_search_provider import FakeSearchProvider
from app.modules.search_intelligence.providers.search.search_provider import (
    SearchAuthorizationGrant,
    SearchPerformanceRequest,
    SearchPerformanceResult,
    SearchPerformanceRow,
    SearchProviderAuthorizationError,
    SearchProviderCapabilities,
    SearchProviderInvalidResponseError,
    SearchProviderTransientError,
)
from app.modules.search_intelligence.providers.search.search_registry_provider import (
    SearchProviderRegistry,
)
from app.modules.search_intelligence.repositories.search_sync_repository import website_page_matches
from app.modules.search_intelligence.services.search_connection_service import _grant_credentials
from app.modules.search_intelligence.services.search_sync_service import (
    SearchSyncScheduler,
    SearchSyncService,
)
from app.modules.workspaces.models import Workspace


class ProcessingProvider(FakeSearchProvider):
    provider_key = "google_search"

    def __init__(self, results: dict[SearchPerformanceRequest, SearchPerformanceResult]) -> None:
        super().__init__(performance_results=results)


@pytest.fixture
async def processing_db() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield sessions
    await engine.dispose()


async def seed_run(
    sessions: async_sessionmaker[AsyncSession],
    vault: DeterministicFakeIntegrationCredentialVault,
    *,
    metric_date: date = date(2026, 7, 1),
    end_date: date | None = None,
    token_expires_at: datetime = datetime(2026, 8, 1, tzinfo=UTC),
    connection_status: str = "active",
) -> UUID:
    async with sessions() as db:
        user = User(email=f"processing-{uuid4()}@example.com")
        workspace = Workspace(name="Processing")
        db.add_all([user, workspace])
        await db.flush()
        product = Product(workspace_id=workspace.id, user_id=user.id, name="Product")
        db.add(product)
        await db.flush()
        website = WebsiteSource(
            workspace_id=workspace.id,
            product_id=product.id,
            base_url="https://example.com/",
            normalized_base_url="https://example.com/",
            status="active",
        )
        grant = SearchAuthorizationGrant(
            "access", "refresh", token_expires_at, ("scope",), "Bearer"
        )
        encrypted = vault.encrypt(_grant_credentials(grant, grant.refresh_token))
        connection = IntegrationConnection(
            workspace_id=workspace.id,
            provider="google_search",
            external_account_id="account",
            idempotency_key=f"google:{workspace.id}",
            credentials_ciphertext=encrypted.ciphertext,
            credential_key_version=encrypted.key_version,
            scopes=list(grant.scopes),
            token_expires_at=grant.expires_at,
            status=connection_status,
        )
        db.add_all([website, connection])
        await db.flush()
        property_row = SearchProviderProperty(
            workspace_id=workspace.id,
            integration_connection_id=connection.id,
            provider="google_search",
            provider_property_id="sc-domain:example.com",
            display_name="example.com",
            property_type="domain",
            permission_level="siteOwner",
            compatibility_status="compatible",
            first_discovered_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        db.add(property_row)
        await db.flush()
        source = SearchSource(
            workspace_id=workspace.id,
            product_id=product.id,
            integration_connection_id=connection.id,
            property_id=property_row.id,
            website_source_id=website.id,
            provider="google_search",
            status="active",
            selected_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        db.add(source)
        await db.flush()
        run = SearchSyncRun(
            workspace_id=workspace.id,
            product_id=product.id,
            source_id=source.id,
            trigger="manual",
            status="pending",
            idempotency_key=f"run:{uuid4()}",
            request_fingerprint="a" * 64,
            requested_start_date=metric_date,
            requested_end_date=end_date or metric_date,
            policy_version="test-v1",
            schema_version=1,
        )
        db.add(run)
        await db.commit()
        return run.id


def row(query: str, page: str, *, clicks: int = 1) -> SearchPerformanceRow:
    return SearchPerformanceRow(
        date(2026, 7, 1),
        query,
        page,
        clicks,
        10,
        Decimal("0.1"),
        Decimal("2.5"),
        "query_page",
    )


@pytest.mark.asyncio
async def test_zero_total_absolute_metrics_dimension_dedupe_and_completed_replay(
    processing_db: async_sessionmaker[AsyncSession],
) -> None:
    vault = DeterministicFakeIntegrationCredentialVault()
    run_id = await seed_run(processing_db, vault)
    total = SearchPerformanceRequest(
        "sc-domain:example.com", date(2026, 7, 1), date(2026, 7, 1), ("date",)
    )
    details = SearchPerformanceRequest(
        "sc-domain:example.com",
        date(2026, 7, 1),
        date(2026, 7, 1),
        ("date", "query", "page"),
    )
    provider = ProcessingProvider(
        {
            total: SearchPerformanceResult((), None, 0),
            details: SearchPerformanceResult(
                (row("widgets", "https://example.com/a?b=1"),) * 2, None, 2
            ),
        }
    )
    service = SearchSyncService(
        sessions=processing_db, providers=SearchProviderRegistry([provider]), vault=vault
    )

    assert (await service.process(run_id))["status"] == "succeeded"
    calls = len(provider.calls)
    assert (await service.process(run_id))["status"] == "succeeded"
    assert len(provider.calls) == calls
    async with processing_db() as db:
        metrics = list(
            await db.scalars(select(SearchDailyMetric).order_by(SearchDailyMetric.grain))
        )
        run = await db.get(SearchSyncRun, run_id)
        assert run is not None
        assert len(metrics) == 2
        assert next(item for item in metrics if item.grain == "site_total").clicks == 0
        assert await db.scalar(select(func.count()).select_from(SearchQuery)) == 1
        assert await db.scalar(select(func.count()).select_from(SearchPage)) == 1
        assert run.progress_date == date(2026, 7, 1)


@pytest.mark.asyncio
async def test_expired_lease_recovers_and_transient_failure_returns_run_to_pending(
    processing_db: async_sessionmaker[AsyncSession],
) -> None:
    vault = DeterministicFakeIntegrationCredentialVault()
    run_id = await seed_run(processing_db, vault)
    async with processing_db() as db:
        run = await db.get(SearchSyncRun, run_id)
        assert run is not None
        run.status = "running"
        run.lease_owner = "dead-worker"
        run.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()

    class TransientProvider(ProcessingProvider):
        async def query_performance(self, grant, request):  # type: ignore[no-untyped-def]
            raise SearchProviderTransientError("temporary")

    provider = TransientProvider({})
    service = SearchSyncService(
        sessions=processing_db, providers=SearchProviderRegistry([provider]), vault=vault
    )
    with pytest.raises(SearchProviderTransientError):
        await service.process(run_id)
    async with processing_db() as db:
        run = await db.get(SearchSyncRun, run_id)
        assert run is not None
        assert run.status == "pending"
        assert run.lease_owner is None
        assert run.lease_expires_at is None


@pytest.mark.asyncio
async def test_unexpected_fault_releases_lease_and_immediate_retry_can_claim(
    processing_db: async_sessionmaker[AsyncSession],
) -> None:
    backing_vault = DeterministicFakeIntegrationCredentialVault()
    run_id = await seed_run(processing_db, backing_vault)

    class FaultOnceVault(DeterministicFakeIntegrationCredentialVault):
        def __init__(self) -> None:
            self.failed = False

        def decrypt(self, ciphertext: str, key_version: str):  # type: ignore[no-untyped-def]
            if not self.failed:
                self.failed = True
                raise RuntimeError("injected vault fault")
            return super().decrypt(ciphertext, key_version)

    total = SearchPerformanceRequest(
        "sc-domain:example.com", date(2026, 7, 1), date(2026, 7, 1), ("date",)
    )
    details = SearchPerformanceRequest(
        "sc-domain:example.com",
        date(2026, 7, 1),
        date(2026, 7, 1),
        ("date", "query", "page"),
    )
    provider = ProcessingProvider(
        {
            total: SearchPerformanceResult((), None, 0),
            details: SearchPerformanceResult((), None, 0),
        }
    )
    vault = FaultOnceVault()
    service = SearchSyncService(
        sessions=processing_db, providers=SearchProviderRegistry([provider]), vault=vault
    )

    with pytest.raises(RuntimeError, match="injected vault fault"):
        await service.process(run_id)
    async with processing_db() as db:
        failed_attempt = await db.get(SearchSyncRun, run_id)
        assert failed_attempt is not None
        assert (
            failed_attempt.status,
            failed_attempt.failure_category,
            failed_attempt.lease_owner,
            failed_attempt.lease_expires_at,
        ) == ("pending", "unexpected", None, None)

    assert await service.process(run_id) == {"status": "succeeded", "runId": str(run_id)}
    assert [call[0] for call in provider.calls].count("query_performance") == 2


@pytest.mark.asyncio
async def test_daily_scheduler_is_deterministic_and_does_not_duplicate_events(
    processing_db: async_sessionmaker[AsyncSession],
) -> None:
    vault = DeterministicFakeIntegrationCredentialVault()
    run_id = await seed_run(processing_db, vault)
    async with processing_db() as db:
        run = await db.get(SearchSyncRun, run_id)
        assert run is not None
        run.status = "succeeded"
        run.started_at = datetime(2026, 7, 1, tzinfo=UTC)
        run.completed_at = datetime(2026, 7, 1, tzinfo=UTC)
        run.is_complete = True
        source = await db.get(SearchSource, run.source_id)
        assert source is not None
        source.latest_successful_data_date = date(2026, 7, 1)
        await db.commit()
    provider = ProcessingProvider({})
    scheduler = SearchSyncScheduler(
        sessions=processing_db,
        providers=SearchProviderRegistry([provider]),
        now=lambda: datetime(2026, 7, 10, tzinfo=UTC),
    )

    assert await scheduler.schedule() == {"scheduled": 1, "skipped": 0}
    assert await scheduler.schedule() == {"scheduled": 1, "skipped": 0}
    async with processing_db() as db:
        assert await db.scalar(select(func.count()).select_from(SearchSyncRun)) == 2
        assert await db.scalar(select(func.count()).select_from(OutboxEvent)) == 1


@pytest.mark.asyncio
async def test_scheduler_filters_inactive_connections_and_uses_reporting_timezone_cutoff(
    processing_db: async_sessionmaker[AsyncSession],
) -> None:
    vault = DeterministicFakeIntegrationCredentialVault()
    run_ids = [
        await seed_run(processing_db, vault, connection_status=status)
        for status in ("active", "expired", "revoked", "error")
    ]
    async with processing_db() as db:
        for run_id in run_ids:
            run = await db.get(SearchSyncRun, run_id)
            assert run is not None
            run.status = "succeeded"
            run.is_complete = True
            run.completed_at = datetime(2026, 7, 8, tzinfo=UTC)
            source = await db.get(SearchSource, run.source_id)
            assert source is not None
            source.latest_successful_data_date = date(2026, 7, 5)
        await db.commit()

    class LosAngelesProvider(ProcessingProvider):
        capabilities = SearchProviderCapabilities(
            True, True, 25_000, 50_000, 3, "America/Los_Angeles"
        )

    result = await SearchSyncScheduler(
        sessions=processing_db,
        providers=SearchProviderRegistry([LosAngelesProvider({})]),
        now=lambda: datetime(2026, 7, 10, 6, 30, tzinfo=UTC),
    ).schedule()

    async with processing_db() as db:
        scheduled_runs = list(
            await db.scalars(select(SearchSyncRun).where(SearchSyncRun.trigger == "scheduled"))
        )
        events = list(await db.scalars(select(OutboxEvent)))
        assert result == {"scheduled": 1, "skipped": 0}
        assert len(scheduled_runs) == len(events) == 1
        assert (
            scheduled_runs[0].requested_start_date,
            scheduled_runs[0].requested_end_date,
        ) == (date(2026, 6, 29), date(2026, 7, 6))


@pytest.mark.asyncio
async def test_pagination_stops_at_daily_cap_and_marks_success_as_truncated(
    processing_db: async_sessionmaker[AsyncSession],
) -> None:
    vault = DeterministicFakeIntegrationCredentialVault()
    run_id = await seed_run(processing_db, vault)

    class CappedProvider(ProcessingProvider):
        capabilities = SearchProviderCapabilities(True, True, 2, 3, 3)

    total_request = SearchPerformanceRequest(
        "sc-domain:example.com", date(2026, 7, 1), date(2026, 7, 1), ("date",), row_limit=2
    )
    first_page = SearchPerformanceRequest(
        "sc-domain:example.com",
        date(2026, 7, 1),
        date(2026, 7, 1),
        ("date", "query", "page"),
        row_limit=2,
    )
    second_page = SearchPerformanceRequest(
        "sc-domain:example.com",
        date(2026, 7, 1),
        date(2026, 7, 1),
        ("date", "query", "page"),
        start_row=2,
        row_limit=1,
    )
    provider = CappedProvider(
        {
            total_request: SearchPerformanceResult((), None, 0),
            first_page: SearchPerformanceResult(
                (
                    row("one", "https://example.com/one"),
                    row("two", "https://example.com/two"),
                ),
                2,
                2,
            ),
            second_page: SearchPerformanceResult(
                (row("three", "https://example.com/three"),), 3, 1
            ),
        }
    )

    result = await SearchSyncService(
        sessions=processing_db, providers=SearchProviderRegistry([provider]), vault=vault
    ).process(run_id)

    async with processing_db() as db:
        run_record = await db.get(SearchSyncRun, run_id)
        assert run_record is not None
        assert result["status"] == "succeeded"
        assert run_record.is_truncated is True
        assert run_record.is_complete is False
        assert await db.scalar(select(func.count()).select_from(SearchDailyMetric)) == 4


@pytest.mark.asyncio
async def test_refreshes_before_metrics_and_revoked_connection_never_calls_provider(
    processing_db: async_sessionmaker[AsyncSession],
) -> None:
    vault = DeterministicFakeIntegrationCredentialVault()
    expired_run = await seed_run(
        processing_db, vault, token_expires_at=datetime(2026, 7, 1, tzinfo=UTC)
    )
    total = SearchPerformanceRequest(
        "sc-domain:example.com", date(2026, 7, 1), date(2026, 7, 1), ("date",)
    )
    details = SearchPerformanceRequest(
        "sc-domain:example.com",
        date(2026, 7, 1),
        date(2026, 7, 1),
        ("date", "query", "page"),
    )

    class RefreshingProvider(ProcessingProvider):
        async def refresh_authorization(
            self, grant: SearchAuthorizationGrant
        ) -> SearchAuthorizationGrant:
            self.calls.append(("refresh_authorization", grant))
            return SearchAuthorizationGrant(
                "refreshed", None, datetime(2026, 9, 1, tzinfo=UTC), ("scope",), "Bearer"
            )

    provider = RefreshingProvider(
        {
            total: SearchPerformanceResult((), None, 0),
            details: SearchPerformanceResult((), None, 0),
        }
    )
    service = SearchSyncService(
        sessions=processing_db,
        providers=SearchProviderRegistry([provider]),
        vault=vault,
        now=lambda: datetime(2026, 7, 27, tzinfo=UTC),
    )
    assert (await service.process(expired_run))["status"] == "succeeded"
    assert provider.calls[0][0] == "refresh_authorization"
    async with processing_db() as db:
        connection = (await db.scalars(select(IntegrationConnection))).one()
        assert connection.credentials_ciphertext is not None
        assert connection.credential_key_version is not None
        refreshed_credentials = vault.decrypt(
            connection.credentials_ciphertext, connection.credential_key_version
        )
        assert refreshed_credentials["refreshToken"] == "refresh"

    revoked_run = await seed_run(processing_db, vault, connection_status="revoked")
    calls_before = list(provider.calls)
    assert (await service.process(revoked_run))["status"] == "failed"
    assert provider.calls == calls_before


@pytest.mark.asyncio
async def test_permanent_authorization_failure_expires_connection(
    processing_db: async_sessionmaker[AsyncSession],
) -> None:
    vault = DeterministicFakeIntegrationCredentialVault()
    run_id = await seed_run(processing_db, vault)

    class UnauthorizedProvider(ProcessingProvider):
        async def query_performance(self, grant, request):  # type: ignore[no-untyped-def]
            raise SearchProviderAuthorizationError("invalid_grant")

    provider = UnauthorizedProvider({})
    result = await SearchSyncService(
        sessions=processing_db, providers=SearchProviderRegistry([provider]), vault=vault
    ).process(run_id)
    async with processing_db() as db:
        run = await db.get(SearchSyncRun, run_id)
        connection = (await db.scalars(select(IntegrationConnection))).one()
        assert run is not None
        assert result["status"] == "failed"
        assert run.failure_category == "invalid_grant"
        assert connection.status == "expired"


@pytest.mark.asyncio
async def test_resumes_after_completed_day_and_permanent_output_failure_is_terminal(
    processing_db: async_sessionmaker[AsyncSession],
) -> None:
    vault = DeterministicFakeIntegrationCredentialVault()
    run_id = await seed_run(processing_db, vault, end_date=date(2026, 7, 2))

    class ResumableProvider(ProcessingProvider):
        def __init__(self) -> None:
            super().__init__({})
            self.fail_second_day = True

        async def query_performance(
            self, grant: SearchAuthorizationGrant, request: SearchPerformanceRequest
        ) -> SearchPerformanceResult:
            self.calls.append(("query_performance", request))
            if (
                request.start_date == date(2026, 7, 2)
                and request.dimensions == ("date", "query", "page")
                and self.fail_second_day
            ):
                self.fail_second_day = False
                raise SearchProviderTransientError("temporary_page_failure")
            return SearchPerformanceResult((), None, 0)

    provider = ResumableProvider()
    service = SearchSyncService(
        sessions=processing_db, providers=SearchProviderRegistry([provider]), vault=vault
    )
    with pytest.raises(SearchProviderTransientError):
        await service.process(run_id)
    async with processing_db() as db:
        failed_attempt = await db.get(SearchSyncRun, run_id)
        assert failed_attempt is not None
        assert failed_attempt.progress_date == date(2026, 7, 1)
        assert failed_attempt.failure_category == "temporary_page_failure"

    calls_before_retry = len(provider.calls)
    assert (await service.process(run_id))["status"] == "succeeded"
    retried_requests = [
        cast(SearchPerformanceRequest, item[1]) for item in provider.calls[calls_before_retry:]
    ]
    assert {request.start_date for request in retried_requests} == {date(2026, 7, 2)}

    permanent_run = await seed_run(processing_db, vault)

    class InvalidProvider(ProcessingProvider):
        async def query_performance(self, grant, request):  # type: ignore[no-untyped-def]
            raise SearchProviderInvalidResponseError("malformed_rows")

    invalid = InvalidProvider({})
    result = await SearchSyncService(
        sessions=processing_db, providers=SearchProviderRegistry([invalid]), vault=vault
    ).process(permanent_run)
    async with processing_db() as db:
        permanent = await db.get(SearchSyncRun, permanent_run)
        assert permanent is not None
        assert result["status"] == "failed"
        assert permanent.status == "failed"
        assert permanent.failure_category == "malformed_rows"


@pytest.mark.asyncio
async def test_url_matching_uses_tier_order_and_never_forces_ambiguity(
    processing_db: async_sessionmaker[AsyncSession],
) -> None:
    vault = DeterministicFakeIntegrationCredentialVault()
    run_id = await seed_run(processing_db, vault)
    async with processing_db() as db:
        sync = await db.get(SearchSyncRun, run_id)
        assert sync is not None
        source = await db.get(SearchSource, sync.source_id)
        assert source is not None
        now = datetime(2026, 7, 1, tzinfo=UTC)
        crawl = WebsiteCrawlRun(
            workspace_id=source.workspace_id,
            product_id=source.product_id,
            source_id=source.website_source_id,
            status="succeeded",
            trigger="manual",
            idempotency_key=f"crawl:{uuid4()}",
            request_fingerprint="b" * 64,
            schema_version=1,
            page_limit=3,
            pages_discovered=3,
            pages_succeeded=3,
            pages_failed=0,
            summary={},
            started_at=now,
            completed_at=now,
        )
        db.add(crawl)
        await db.flush()
        pages = [
            WebsitePage(
                workspace_id=source.workspace_id,
                product_id=source.product_id,
                source_id=source.website_source_id,
                first_discovered_run_id=crawl.id,
                url="https://example.com/shared",
                canonical_url=f"https://example.com/canonical-{index}",
                url_fingerprint=str(index) * 64,
                status="active",
            )
            for index in (1, 2, 3)
        ]
        pages[2].url = "https://example.com/redirected"
        db.add_all(pages)
        await db.flush()
        db.add(
            WebsiteCrawlResult(
                workspace_id=source.workspace_id,
                product_id=source.product_id,
                source_id=source.website_source_id,
                crawl_run_id=crawl.id,
                page_id=pages[2].id,
                status="succeeded",
                final_url="https://example.com/final?campaign=one",
                http_status=200,
                content_fingerprint="c" * 64,
                fetched_at=now,
            )
        )
        await db.commit()

        assert (
            await website_page_matches(
                db,
                workspace_id=source.workspace_id,
                product_id=source.product_id,
                website_source_id=source.website_source_id,
                normalized_url="https://example.com/canonical-1",
            )
        )[0] == "canonical_url"
        assert await website_page_matches(
            db,
            workspace_id=source.workspace_id,
            product_id=source.product_id,
            website_source_id=source.website_source_id,
            normalized_url="https://example.com/shared",
        ) == ("ambiguous", None)
        assert (
            await website_page_matches(
                db,
                workspace_id=source.workspace_id,
                product_id=source.product_id,
                website_source_id=source.website_source_id,
                normalized_url="https://example.com/final?campaign=one",
            )
        )[0] == "crawl_final_url"
        assert await website_page_matches(
            db,
            workspace_id=source.workspace_id,
            product_id=source.product_id,
            website_source_id=source.website_source_id,
            normalized_url="https://example.com/missing?keep=true",
        ) == ("unmatched", None)
