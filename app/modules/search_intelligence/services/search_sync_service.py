import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Table, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.idempotency import insert_or_replay
from app.db.outbox import OutboxMetadata, enqueue_outbox_event
from app.modules.integrations.models import IntegrationConnection
from app.modules.integrations.providers.credential_vault_provider import (
    CredentialVaultError,
    IntegrationCredentialVault,
)
from app.modules.products.models import Product
from app.modules.products.policies.website_url_policy import normalize_website_url
from app.modules.search_intelligence.models import (
    SearchDailyMetric,
    SearchPage,
    SearchQuery,
    SearchSource,
    SearchSyncRun,
    search_sync_runs,
)
from app.modules.search_intelligence.policies.search_sync_policy import (
    SYNC_POLICY_VERSION,
    search_sync_window,
)
from app.modules.search_intelligence.providers.search.search_provider import (
    SearchAuthorizationGrant,
    SearchPerformanceRequest,
    SearchPerformanceRow,
    SearchProvider,
    SearchProviderAuthorizationError,
    SearchProviderCapabilities,
    SearchProviderError,
    SearchProviderTransientError,
)
from app.modules.search_intelligence.providers.search.search_registry_provider import (
    SearchProviderRegistry,
)
from app.modules.search_intelligence.repositories.search_connection_repository import (
    get_active_search_source,
)
from app.modules.search_intelligence.repositories.search_sync_repository import (
    daily_metric,
    get_active_run,
    get_run_by_idempotency,
    get_sync_run,
    list_scoped_sync_runs,
    load_processing_context,
    page_identity,
    query_identity,
    website_page_matches,
)
from app.modules.search_intelligence.services.search_connection_service import (
    _grant_credentials,
    decode_grant,
)
from app.shared.exceptions import BadRequestError, ConflictError, NotFoundError

MAX_SYNC_DAYS = 90
LEASE_DURATION = timedelta(minutes=15)
REFRESH_SKEW = timedelta(minutes=5)
MATCH_POLICY_VERSION = "exact-url-v1"


def _fingerprint(*, trigger: str, start_date: date, end_date: date, source_id: UUID) -> str:
    payload = json.dumps(
        {
            "endDate": end_date.isoformat(),
            "sourceId": str(source_id),
            "startDate": start_date.isoformat(),
            "trigger": trigger,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def request_search_sync(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    idempotency_key: str,
    trigger: str,
    capabilities: SearchProviderCapabilities,
    actor_id: UUID | None,
    requested_start_date: date | None = None,
    requested_end_date: date | None = None,
    now: datetime | None = None,
) -> SearchSyncRun:
    if not idempotency_key or len(idempotency_key) > 255:
        raise BadRequestError(
            "A valid Idempotency-Key is required.", code="idempotency_key_invalid"
        )
    current = now or datetime.now(UTC)
    reporting_zone = ZoneInfo(capabilities.reporting_timezone)
    reporting_today = current.astimezone(reporting_zone).date()
    source = await get_active_search_source(db, workspace_id, product_id, for_update=True)
    if source is None:
        raise ConflictError("An active search source is required.", code="search_source_inactive")

    if (requested_start_date is None) != (requested_end_date is None):
        raise BadRequestError(
            "Both sync dates are required together.", code="search_sync_dates_invalid"
        )
    if requested_start_date is None:
        window = search_sync_window(
            trigger=trigger,
            today=reporting_today,
            finalization_lag_days=capabilities.finalization_lag_days,
            selected_at=(
                source.selected_at.replace(tzinfo=UTC)
                if source.selected_at.tzinfo is None
                else source.selected_at
            ).astimezone(reporting_zone),
            latest_successful_data_date=source.latest_successful_data_date,
        )
        if window is None:
            raise ConflictError(
                "No complete search dates are available.", code="search_sync_window_empty"
            )
        start_date, end_date = window
    else:
        assert requested_end_date is not None
        start_date, end_date = requested_start_date, requested_end_date
        cutoff = reporting_today - timedelta(days=capabilities.finalization_lag_days)
        if start_date > end_date or end_date > cutoff:
            raise BadRequestError(
                "Search sync dates are invalid.", code="search_sync_dates_invalid"
            )
    if (end_date - start_date).days + 1 > MAX_SYNC_DAYS:
        raise BadRequestError(
            "Search sync window is too large.", code="search_sync_window_too_large"
        )

    fingerprint = _fingerprint(
        trigger=trigger, start_date=start_date, end_date=end_date, source_id=source.id
    )
    replay = await get_run_by_idempotency(db, workspace_id, source.id, idempotency_key)
    if replay is not None:
        if replay.request_fingerprint != fingerprint:
            raise ConflictError(
                "Idempotency key was used for a different request.",
                code="idempotency_key_conflict",
            )
        await db.commit()
        return replay
    if await get_active_run(db, workspace_id, source.id) is not None:
        raise ConflictError("A search sync is already active.", code="search_sync_active")

    run_id = uuid.uuid4()
    try:
        result = await insert_or_replay(
            db,
            cast(Table, search_sync_runs),
            values={
                "id": run_id,
                "workspace_id": workspace_id,
                "product_id": product_id,
                "source_id": source.id,
                "trigger": trigger,
                "status": "pending",
                "idempotency_key": idempotency_key,
                "request_fingerprint": fingerprint,
                "requested_start_date": start_date,
                "requested_end_date": end_date,
                "policy_version": SYNC_POLICY_VERSION,
                "schema_version": 1,
                "rows_received": 0,
                "rows_written": 0,
                "is_complete": False,
                "is_truncated": False,
                "created_by": actor_id,
                "updated_by": actor_id,
                "created_at": current,
                "updated_at": current,
            },
            conflict_columns=("workspace_id", "source_id", "idempotency_key"),
        )
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("A search sync is already active.", code="search_sync_active") from exc
    run = await db.get(SearchSyncRun, result.value["id"], populate_existing=True)
    if run is None:
        raise RuntimeError("search sync insert succeeded without a readable run")
    if run.request_fingerprint != fingerprint:
        raise ConflictError(
            "Idempotency key was used for a different request.", code="idempotency_key_conflict"
        )
    if result.inserted:
        await enqueue_outbox_event(
            db,
            event_name="search/metrics.sync.requested",
            schema_version=1,
            idempotency_key=str(run.id),
            aggregate_id=run.id,
            workspace_id=workspace_id,
            product_id=product_id,
            metadata=OutboxMetadata(
                {
                    "runId": str(run.id),
                    "workspaceId": str(workspace_id),
                    "productId": str(product_id),
                    "sourceId": str(source.id),
                    "schemaVersion": 1,
                    "correlationId": str(run.id),
                }
            ),
        )
    await db.commit()
    await db.refresh(run)
    return run


async def read_search_sync(
    db: AsyncSession, *, workspace_id: UUID, product_id: UUID, run_id: UUID
) -> SearchSyncRun:
    run = await get_sync_run(db, workspace_id, product_id, run_id)
    if run is None:
        raise NotFoundError("Search sync not found.", code="search_sync_not_found")
    return run


async def list_search_syncs(
    db: AsyncSession, *, workspace_id: UUID, product_id: UUID
) -> list[SearchSyncRun]:
    return await list_scoped_sync_runs(db, workspace_id, product_id)


async def create_initial_sync(
    db: AsyncSession,
    *,
    source: SearchSource,
    capabilities: SearchProviderCapabilities,
    now: datetime | None = None,
) -> SearchSyncRun:
    return await request_search_sync(
        db,
        workspace_id=source.workspace_id,
        product_id=source.product_id,
        idempotency_key=f"initial:{source.id}",
        trigger="initial",
        capabilities=capabilities,
        actor_id=source.created_by,
        now=now,
    )


class SearchSyncService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        providers: SearchProviderRegistry,
        vault: IntegrationCredentialVault,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_duration: timedelta = LEASE_DURATION,
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._vault = vault
        self._now = now
        self._lease_duration = lease_duration

    async def process(self, run_id: UUID) -> dict[str, object]:
        owner = str(uuid.uuid4())
        claimed = await self._claim(run_id, owner)
        if claimed is None:
            async with self._sessions() as db:
                replay = await db.get(SearchSyncRun, run_id)
            if replay is not None and replay.status in {"pending", "running"}:
                raise SearchProviderTransientError("search_sync_concurrent")
            return {
                "status": replay.status if replay is not None else "noop",
                "runId": str(run_id),
            }
        try:
            async with self._sessions() as db:
                context = await load_processing_context(db, run_id)
            if context is None:
                raise ValueError("Search sync run was not found.")
            run, source, property_record, connection = context
            if (
                source.status != "active"
                or connection.status != "active"
                or connection.revoked_at is not None
                or connection.deleted_at is not None
            ):
                await self._fail(run_id, owner, "authorization_inactive")
                return {"status": "failed", "runId": str(run_id)}
            provider = self._providers.get(source.provider)
            grant = await self._usable_grant(connection, provider, source.created_by)
            next_date = (
                run.requested_start_date
                if run.progress_date is None
                else run.progress_date + timedelta(days=1)
            )
            while next_date <= run.requested_end_date:
                total_rows = await self._fetch(
                    provider, grant, property_record.provider_property_id, next_date, ("date",)
                )
                query_rows, truncated = await self._fetch_query_pages(
                    provider, grant, property_record.provider_property_id, next_date
                )
                await self._persist_day(
                    run_id,
                    owner,
                    source,
                    next_date,
                    total_rows,
                    query_rows,
                    truncated=truncated,
                )
                next_date += timedelta(days=1)
            await self._succeed(run_id, owner)
            return {"status": "succeeded", "runId": str(run_id)}
        except SearchProviderTransientError as exc:
            await self._release(run_id, owner, exc.code)
            raise
        except SearchProviderAuthorizationError as exc:
            await self._authorization_failed(run_id, owner, exc.code)
            return {"status": "failed", "runId": str(run_id)}
        except SearchProviderError as exc:
            await self._fail(run_id, owner, exc.code)
            return {"status": "failed", "runId": str(run_id)}
        except ValueError:
            await self._fail(run_id, owner, "invalid_provider_output")
            return {"status": "failed", "runId": str(run_id)}
        except Exception:
            await self._release(run_id, owner, "unexpected")
            raise

    async def _claim(self, run_id: UUID, owner: str) -> SearchSyncRun | None:
        async with self._sessions() as db:
            run = await db.scalar(
                select(SearchSyncRun).where(SearchSyncRun.id == run_id).with_for_update()
            )
            if run is None:
                raise ValueError("Search sync run was not found.")
            now = self._now()
            if run.status in {"succeeded", "cancelled", "failed"}:
                return None
            lease_expires_at = run.lease_expires_at
            if lease_expires_at is not None and lease_expires_at.tzinfo is None:
                lease_expires_at = lease_expires_at.replace(tzinfo=UTC)
            if lease_expires_at is not None and lease_expires_at > now:
                return None
            run.status = "running"
            run.started_at = run.started_at or now
            run.lease_owner = owner
            run.lease_expires_at = now + self._lease_duration
            run.failure_category = None
            await db.commit()
            return run

    async def _usable_grant(
        self, connection: IntegrationConnection, provider: SearchProvider, actor_id: UUID | None
    ) -> SearchAuthorizationGrant:
        if not connection.credentials_ciphertext or not connection.credential_key_version:
            raise SearchProviderError("credentials_unavailable")
        try:
            grant = decode_grant(
                dict(
                    self._vault.decrypt(
                        connection.credentials_ciphertext, connection.credential_key_version
                    )
                )
            )
        except (CredentialVaultError, BadRequestError) as exc:
            raise RuntimeError("search credential processing failed") from exc
        if grant.expires_at is None or grant.expires_at > self._now() + REFRESH_SKEW:
            return grant
        refreshed = await provider.refresh_authorization(grant)
        refresh_token = refreshed.refresh_token or grant.refresh_token
        encrypted = self._vault.encrypt(_grant_credentials(refreshed, refresh_token))
        async with self._sessions() as db:
            locked = await db.scalar(
                select(IntegrationConnection)
                .where(
                    IntegrationConnection.workspace_id == connection.workspace_id,
                    IntegrationConnection.id == connection.id,
                )
                .with_for_update()
            )
            if locked is None or locked.status != "active" or locked.revoked_at is not None:
                raise SearchProviderError("authorization_inactive")
            locked.credentials_ciphertext = encrypted.ciphertext
            locked.credential_key_version = encrypted.key_version
            locked.scopes = list(refreshed.scopes)
            locked.token_expires_at = refreshed.expires_at
            locked.updated_by = actor_id
            await db.commit()
        return SearchAuthorizationGrant(
            refreshed.access_token,
            refresh_token,
            refreshed.expires_at,
            refreshed.scopes,
            refreshed.token_type,
        )

    @staticmethod
    async def _fetch(
        provider: SearchProvider,
        grant: SearchAuthorizationGrant,
        property_id: str,
        metric_date: date,
        dimensions: tuple[str, ...],
        *,
        start_row: int = 0,
    ) -> tuple[SearchPerformanceRow, ...]:
        result = await provider.query_performance(
            grant,
            SearchPerformanceRequest(
                property_id=property_id,
                start_date=metric_date,
                end_date=metric_date,
                dimensions=dimensions,
                start_row=start_row,
                row_limit=provider.capabilities.request_page_limit,
            ),
        )
        if any(row.date != metric_date for row in result.rows):
            raise ValueError("provider returned a row outside the requested date")
        return result.rows

    async def _fetch_query_pages(
        self,
        provider: SearchProvider,
        grant: SearchAuthorizationGrant,
        property_id: str,
        metric_date: date,
    ) -> tuple[tuple[SearchPerformanceRow, ...], bool]:
        rows: list[SearchPerformanceRow] = []
        start = 0
        cap = provider.capabilities.maximum_rows_per_day
        while len(rows) < cap:
            result = await provider.query_performance(
                grant,
                SearchPerformanceRequest(
                    property_id=property_id,
                    start_date=metric_date,
                    end_date=metric_date,
                    dimensions=("date", "query", "page"),
                    start_row=start,
                    row_limit=min(provider.capabilities.request_page_limit, cap - len(rows)),
                ),
            )
            if any(
                row.date != metric_date or row.query is None or row.page is None
                for row in result.rows
            ):
                raise ValueError("provider returned invalid query-page dimensions")
            rows.extend(result.rows)
            if result.next_start_row is None:
                return tuple(rows), False
            if result.next_start_row <= start:
                raise ValueError("provider pagination did not advance")
            start = result.next_start_row
        return tuple(rows), True

    async def _persist_day(
        self,
        run_id: UUID,
        lease_owner: str,
        source: SearchSource,
        metric_date: date,
        totals: tuple[SearchPerformanceRow, ...],
        rows: tuple[SearchPerformanceRow, ...],
        *,
        truncated: bool,
    ) -> None:
        if len(totals) > 1 or any(row.query is not None or row.page is not None for row in totals):
            raise ValueError("invalid site total result")
        total = totals[0] if totals else None
        async with self._sessions() as db:
            run = await db.scalar(
                select(SearchSyncRun).where(SearchSyncRun.id == run_id).with_for_update()
            )
            if run is None or run.status != "running" or run.lease_owner != lease_owner:
                raise ValueError("search sync lease is no longer active")
            site_metric = await daily_metric(
                db,
                workspace_id=source.workspace_id,
                source_id=source.id,
                metric_date=metric_date,
                query_id=None,
                page_id=None,
            )
            if site_metric is None:
                site_metric = SearchDailyMetric(
                    workspace_id=source.workspace_id,
                    product_id=source.product_id,
                    source_id=source.id,
                    metric_date=metric_date,
                    search_type="web",
                    grain="site_total",
                    clicks=0,
                    impressions=0,
                )
                db.add(site_metric)
            site_metric.clicks = total.clicks if total else 0
            site_metric.impressions = total.impressions if total else 0
            site_metric.average_position = total.position if total else None
            await db.execute(
                delete(SearchDailyMetric).where(
                    SearchDailyMetric.workspace_id == source.workspace_id,
                    SearchDailyMetric.source_id == source.id,
                    SearchDailyMetric.metric_date == metric_date,
                    SearchDailyMetric.search_type == "web",
                    SearchDailyMetric.grain == "query_page",
                )
            )
            written = 1
            written_dimensions: set[tuple[UUID, UUID]] = set()
            for row in rows:
                assert row.query is not None and row.page is not None
                query_key = hashlib.sha256(row.query.encode()).hexdigest()
                query = await query_identity(db, source.workspace_id, source.id, query_key)
                if query is None:
                    query_result = await insert_or_replay(
                        db,
                        cast(Table, SearchQuery.__table__),
                        values={
                            "id": uuid.uuid4(),
                            "workspace_id": source.workspace_id,
                            "product_id": source.product_id,
                            "source_id": source.id,
                            "query_text": row.query,
                            "query_fingerprint": query_key,
                        },
                        conflict_columns=("workspace_id", "source_id", "query_fingerprint"),
                    )
                    query = await db.get(
                        SearchQuery, query_result.value["id"], populate_existing=True
                    )
                    if query is None:
                        raise RuntimeError("query identity was not readable after insert")
                normalized_url = normalize_website_url(row.page)
                page_key = hashlib.sha256(normalized_url.encode()).hexdigest()
                page = await page_identity(db, source.workspace_id, source.id, page_key)
                if page is None:
                    method, website_page = await website_page_matches(
                        db,
                        workspace_id=source.workspace_id,
                        product_id=source.product_id,
                        website_source_id=source.website_source_id,
                        normalized_url=normalized_url,
                    )
                    page_result = await insert_or_replay(
                        db,
                        cast(Table, SearchPage.__table__),
                        values={
                            "id": uuid.uuid4(),
                            "workspace_id": source.workspace_id,
                            "product_id": source.product_id,
                            "source_id": source.id,
                            "original_url": row.page,
                            "normalized_url": normalized_url,
                            "url_fingerprint": page_key,
                            "website_source_id": (
                                source.website_source_id if website_page else None
                            ),
                            "website_page_id": website_page.id if website_page else None,
                            "match_status": (
                                "matched"
                                if website_page is not None
                                else "ambiguous"
                                if method == "ambiguous"
                                else "unmatched"
                            ),
                            "match_method": method if method != "unmatched" else None,
                            "match_policy_version": MATCH_POLICY_VERSION,
                        },
                        conflict_columns=("workspace_id", "source_id", "url_fingerprint"),
                    )
                    page = await db.get(SearchPage, page_result.value["id"], populate_existing=True)
                    if page is None:
                        raise RuntimeError("page identity was not readable after insert")
                metric = await daily_metric(
                    db,
                    workspace_id=source.workspace_id,
                    source_id=source.id,
                    metric_date=metric_date,
                    query_id=query.id,
                    page_id=page.id,
                )
                if metric is None:
                    metric = SearchDailyMetric(
                        workspace_id=source.workspace_id,
                        product_id=source.product_id,
                        source_id=source.id,
                        query_id=query.id,
                        page_id=page.id,
                        metric_date=metric_date,
                        search_type="web",
                        grain="query_page",
                        clicks=0,
                        impressions=0,
                    )
                    db.add(metric)
                    await db.flush()
                metric.clicks = row.clicks
                metric.impressions = row.impressions
                metric.average_position = row.position
                dimensions = (query.id, page.id)
                if dimensions not in written_dimensions:
                    written += 1
                    written_dimensions.add(dimensions)
            run.rows_received += len(totals) + len(rows)
            run.rows_written += written
            run.is_truncated = run.is_truncated or truncated
            run.progress_date = metric_date
            run.lease_expires_at = self._now() + self._lease_duration
            persisted_source = await db.get(SearchSource, source.id)
            if persisted_source is not None:
                persisted_source.latest_successful_data_date = metric_date
            await db.commit()

    async def _succeed(self, run_id: UUID, lease_owner: str) -> None:
        async with self._sessions() as db:
            run = await db.scalar(
                select(SearchSyncRun).where(SearchSyncRun.id == run_id).with_for_update()
            )
            if run is None or run.lease_owner != lease_owner:
                return
            run.status = "succeeded"
            run.is_complete = not run.is_truncated
            run.completed_at = self._now()
            run.lease_owner = None
            run.lease_expires_at = None
            source = await db.scalar(
                select(SearchSource).where(
                    SearchSource.workspace_id == run.workspace_id,
                    SearchSource.product_id == run.product_id,
                    SearchSource.id == run.source_id,
                )
            )
            if source is not None:
                source.latest_successful_data_date = run.requested_end_date
                product = await db.scalar(
                    select(Product).where(
                        Product.workspace_id == run.workspace_id,
                        Product.id == run.product_id,
                    )
                )
                if product is not None:
                    product.search_mode = "connected"
                    product.search_mode_decided_at = self._now()
            await db.commit()

    async def _release(self, run_id: UUID, lease_owner: str, category: str) -> None:
        await self._finish_failure(run_id, lease_owner, category, terminal=False)

    async def _authorization_failed(self, run_id: UUID, lease_owner: str, category: str) -> None:
        await self._finish_failure(
            run_id, lease_owner, category, terminal=True, expire_connection=True
        )

    async def _fail(self, run_id: UUID, lease_owner: str, category: str) -> None:
        await self._finish_failure(run_id, lease_owner, category, terminal=True)

    async def _finish_failure(
        self,
        run_id: UUID,
        lease_owner: str,
        category: str,
        *,
        terminal: bool,
        expire_connection: bool = False,
    ) -> None:
        safe = "".join(character if character.isalnum() else "_" for character in category.lower())
        safe = safe.strip("_")[:64] or "unknown"
        async with self._sessions() as db:
            run = await db.scalar(
                select(SearchSyncRun).where(SearchSyncRun.id == run_id).with_for_update()
            )
            if (
                run is None
                or run.status in {"succeeded", "cancelled"}
                or run.lease_owner != lease_owner
            ):
                return
            run.failure_category = safe
            run.lease_owner = None
            run.lease_expires_at = None
            if terminal:
                run.status = "failed"
                run.completed_at = self._now()
            else:
                run.status = "pending"
            if expire_connection:
                source = await db.get(SearchSource, run.source_id)
                if source is not None:
                    connection = await db.get(
                        IntegrationConnection, source.integration_connection_id
                    )
                    if connection is not None:
                        connection.status = "expired"
            await db.commit()


class SearchSyncScheduler:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        providers: SearchProviderRegistry,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._now = now

    async def schedule(self) -> dict[str, int]:
        current = self._now()
        async with self._sessions() as db:
            source_refs = list(
                (
                    await db.execute(
                        select(SearchSource.id, SearchSource.provider)
                        .join(
                            IntegrationConnection,
                            (IntegrationConnection.workspace_id == SearchSource.workspace_id)
                            & (IntegrationConnection.id == SearchSource.integration_connection_id),
                        )
                        .where(
                            SearchSource.status == "active",
                            SearchSource.deleted_at.is_(None),
                            IntegrationConnection.status == "active",
                            IntegrationConnection.revoked_at.is_(None),
                            IntegrationConnection.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
        scheduled = 0
        skipped = 0
        for source_id, provider_key in source_refs:
            provider = self._providers.get(provider_key)
            reporting_date = current.astimezone(
                ZoneInfo(provider.capabilities.reporting_timezone)
            ).date()
            cutoff = reporting_date - timedelta(days=provider.capabilities.finalization_lag_days)
            async with self._sessions() as db:
                source = await db.get(SearchSource, source_id)
                if source is None:
                    skipped += 1
                    continue
                try:
                    await request_search_sync(
                        db,
                        workspace_id=source.workspace_id,
                        product_id=source.product_id,
                        idempotency_key=f"scheduled:{source.id}:{cutoff.isoformat()}",
                        trigger="scheduled",
                        capabilities=provider.capabilities,
                        actor_id=None,
                        now=current,
                    )
                    await db.commit()
                    scheduled += 1
                except ConflictError as exc:
                    await db.rollback()
                    if exc.code not in {"search_sync_active", "search_sync_window_empty"}:
                        raise
                    skipped += 1
        return {"scheduled": scheduled, "skipped": skipped}


__all__ = [
    "SearchSyncScheduler",
    "SearchSyncService",
    "create_initial_sync",
    "list_search_syncs",
    "read_search_sync",
    "request_search_sync",
]
