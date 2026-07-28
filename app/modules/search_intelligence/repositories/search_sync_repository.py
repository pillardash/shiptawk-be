from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.idempotency import InsertReplayResult, insert_or_replay
from app.modules.integrations.models import IntegrationConnection
from app.modules.products.models import WebsiteCrawlResult, WebsitePage
from app.modules.search_intelligence.models import (
    SearchDailyMetric,
    SearchPage,
    SearchProviderProperty,
    SearchQuery,
    SearchSource,
    SearchSyncRun,
)


async def insert_or_replay_sync_run(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    source_id: UUID,
    trigger: str,
    idempotency_key: str,
    request_fingerprint: str,
    start_date: object,
    end_date: object,
    policy_version: str,
) -> InsertReplayResult[SearchSyncRun]:
    result = await insert_or_replay(
        db,
        cast(Table, SearchSyncRun.__table__),
        values={
            "id": uuid4(),
            "workspace_id": workspace_id,
            "product_id": product_id,
            "source_id": source_id,
            "trigger": trigger,
            "status": "pending",
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "requested_start_date": start_date,
            "requested_end_date": end_date,
            "policy_version": policy_version,
            "schema_version": 1,
            "rows_received": 0,
            "rows_written": 0,
            "is_complete": False,
            "is_truncated": False,
        },
        conflict_columns=("workspace_id", "source_id", "idempotency_key"),
    )
    run = await db.get(SearchSyncRun, result.value["id"], populate_existing=True)
    if run is None:
        raise RuntimeError("sync insert succeeded without a readable run")
    return InsertReplayResult(run, result.inserted)


async def get_source_initial_run(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, source_id: UUID
) -> SearchSyncRun | None:
    return cast(
        SearchSyncRun | None,
        await db.scalar(
            select(SearchSyncRun)
            .where(
                SearchSyncRun.workspace_id == workspace_id,
                SearchSyncRun.product_id == product_id,
                SearchSyncRun.source_id == source_id,
                SearchSyncRun.trigger == "initial",
                SearchSyncRun.deleted_at.is_(None),
            )
            .order_by(SearchSyncRun.created_at.asc(), SearchSyncRun.id.asc())
            .limit(1)
        ),
    )


async def get_scoped_sync_run(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, run_id: UUID
) -> SearchSyncRun | None:
    return cast(
        SearchSyncRun | None,
        await db.scalar(
            select(SearchSyncRun).where(
                SearchSyncRun.workspace_id == workspace_id,
                SearchSyncRun.product_id == product_id,
                SearchSyncRun.id == run_id,
                SearchSyncRun.deleted_at.is_(None),
            )
        ),
    )


get_sync_run = get_scoped_sync_run


async def get_run_by_idempotency(
    db: AsyncSession, workspace_id: UUID, source_id: UUID, idempotency_key: str
) -> SearchSyncRun | None:
    return cast(
        SearchSyncRun | None,
        await db.scalar(
            select(SearchSyncRun).where(
                SearchSyncRun.workspace_id == workspace_id,
                SearchSyncRun.source_id == source_id,
                SearchSyncRun.idempotency_key == idempotency_key,
            )
        ),
    )


async def get_active_run(
    db: AsyncSession, workspace_id: UUID, source_id: UUID
) -> SearchSyncRun | None:
    return cast(
        SearchSyncRun | None,
        await db.scalar(
            select(SearchSyncRun).where(
                SearchSyncRun.workspace_id == workspace_id,
                SearchSyncRun.source_id == source_id,
                SearchSyncRun.status.in_(("pending", "running")),
            )
        ),
    )


async def load_processing_context(
    db: AsyncSession, run_id: UUID
) -> tuple[SearchSyncRun, SearchSource, SearchProviderProperty, IntegrationConnection] | None:
    row = (
        await db.execute(
            select(SearchSyncRun, SearchSource, SearchProviderProperty, IntegrationConnection)
            .join(
                SearchSource,
                (SearchSource.workspace_id == SearchSyncRun.workspace_id)
                & (SearchSource.product_id == SearchSyncRun.product_id)
                & (SearchSource.id == SearchSyncRun.source_id),
            )
            .join(
                SearchProviderProperty,
                (SearchProviderProperty.workspace_id == SearchSource.workspace_id)
                & (
                    SearchProviderProperty.integration_connection_id
                    == SearchSource.integration_connection_id
                )
                & (SearchProviderProperty.id == SearchSource.property_id),
            )
            .join(
                IntegrationConnection,
                (IntegrationConnection.workspace_id == SearchSource.workspace_id)
                & (IntegrationConnection.id == SearchSource.integration_connection_id),
            )
            .where(SearchSyncRun.id == run_id)
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1], row[2], row[3])


async def website_page_matches(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    website_source_id: UUID,
    normalized_url: str,
) -> tuple[str, WebsitePage | None]:
    common = (
        WebsitePage.workspace_id == workspace_id,
        WebsitePage.product_id == product_id,
        WebsitePage.source_id == website_source_id,
        WebsitePage.deleted_at.is_(None),
    )
    canonical = list(
        await db.scalars(
            select(WebsitePage).where(*common, WebsitePage.canonical_url == normalized_url)
        )
    )
    if len(canonical) == 1:
        return "canonical_url", canonical[0]
    if len(canonical) > 1:
        return "ambiguous", None
    observed = list(
        await db.scalars(select(WebsitePage).where(*common, WebsitePage.url == normalized_url))
    )
    if len(observed) == 1:
        return "observed_url", observed[0]
    if len(observed) > 1:
        return "ambiguous", None
    final_pages = list(
        await db.scalars(
            select(WebsitePage)
            .join(
                WebsiteCrawlResult,
                (WebsiteCrawlResult.workspace_id == WebsitePage.workspace_id)
                & (WebsiteCrawlResult.product_id == WebsitePage.product_id)
                & (WebsiteCrawlResult.source_id == WebsitePage.source_id)
                & (WebsiteCrawlResult.page_id == WebsitePage.id),
            )
            .where(
                *common,
                WebsiteCrawlResult.status == "succeeded",
                WebsiteCrawlResult.final_url == normalized_url,
                WebsiteCrawlResult.deleted_at.is_(None),
            )
        )
    )
    unique = {page.id: page for page in final_pages}
    if len(unique) == 1:
        return "crawl_final_url", next(iter(unique.values()))
    return ("ambiguous", None) if unique else ("unmatched", None)


async def query_identity(
    db: AsyncSession, workspace_id: UUID, source_id: UUID, fingerprint: str
) -> SearchQuery | None:
    return cast(
        SearchQuery | None,
        await db.scalar(
            select(SearchQuery).where(
                SearchQuery.workspace_id == workspace_id,
                SearchQuery.source_id == source_id,
                SearchQuery.query_fingerprint == fingerprint,
            )
        ),
    )


async def page_identity(
    db: AsyncSession, workspace_id: UUID, source_id: UUID, fingerprint: str
) -> SearchPage | None:
    return cast(
        SearchPage | None,
        await db.scalar(
            select(SearchPage).where(
                SearchPage.workspace_id == workspace_id,
                SearchPage.source_id == source_id,
                SearchPage.url_fingerprint == fingerprint,
            )
        ),
    )


async def daily_metric(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    source_id: UUID,
    metric_date: object,
    query_id: UUID | None,
    page_id: UUID | None,
) -> SearchDailyMetric | None:
    conditions = [
        SearchDailyMetric.workspace_id == workspace_id,
        SearchDailyMetric.source_id == source_id,
        SearchDailyMetric.metric_date == metric_date,
        SearchDailyMetric.search_type == "web",
        SearchDailyMetric.query_id.is_(None)
        if query_id is None
        else SearchDailyMetric.query_id == query_id,
        SearchDailyMetric.page_id.is_(None)
        if page_id is None
        else SearchDailyMetric.page_id == page_id,
    ]
    return cast(
        SearchDailyMetric | None,
        await db.scalar(select(SearchDailyMetric).where(*conditions)),
    )


async def list_active_sources_for_schedule(db: AsyncSession):  # type: ignore[no-untyped-def]
    from app.modules.search_intelligence.models import SearchSource

    return list(
        await db.scalars(
            select(SearchSource)
            .where(SearchSource.status == "active", SearchSource.deleted_at.is_(None))
            .order_by(SearchSource.workspace_id, SearchSource.id)
        )
    )


__all__ = [
    "daily_metric",
    "get_active_run",
    "get_run_by_idempotency",
    "get_scoped_sync_run",
    "get_source_initial_run",
    "get_sync_run",
    "insert_or_replay_sync_run",
    "list_active_sources_for_schedule",
    "load_processing_context",
    "page_identity",
    "query_identity",
    "website_page_matches",
]
