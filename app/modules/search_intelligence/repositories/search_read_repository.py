from datetime import date
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integrations.models import IntegrationConnection
from app.modules.search_intelligence.models import (
    SearchDailyMetric,
    SearchPage,
    SearchProviderProperty,
    SearchQuery,
    SearchSource,
    SearchSyncRun,
)


async def latest_source(
    db: AsyncSession, workspace_id: UUID, product_id: UUID
) -> SearchSource | None:
    return cast(
        SearchSource | None,
        await db.scalar(
            select(SearchSource)
            .where(
                SearchSource.workspace_id == workspace_id,
                SearchSource.product_id == product_id,
                SearchSource.deleted_at.is_(None),
            )
            .order_by(SearchSource.selected_at.desc(), SearchSource.id.desc())
            .limit(1)
        ),
    )


async def latest_provider_connection(
    db: AsyncSession, workspace_id: UUID, provider: str
) -> IntegrationConnection | None:
    return cast(
        IntegrationConnection | None,
        await db.scalar(
            select(IntegrationConnection)
            .where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.provider == provider,
                IntegrationConnection.deleted_at.is_(None),
            )
            .order_by(IntegrationConnection.updated_at.desc(), IntegrationConnection.id.desc())
            .limit(1)
        ),
    )


async def metrics_for_range(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    source_id: UUID,
    start_date: date,
    end_date: date,
) -> list[SearchDailyMetric]:
    return list(
        await db.scalars(
            select(SearchDailyMetric).where(
                SearchDailyMetric.workspace_id == workspace_id,
                SearchDailyMetric.product_id == product_id,
                SearchDailyMetric.source_id == source_id,
                SearchDailyMetric.metric_date.between(start_date, end_date),
                SearchDailyMetric.search_type == "web",
            )
        )
    )


async def metric_date_bounds(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, source_id: UUID
) -> tuple[date | None, date | None]:
    row = (
        await db.execute(
            select(
                func.min(SearchDailyMetric.metric_date),
                func.max(SearchDailyMetric.metric_date),
            ).where(
                SearchDailyMetric.workspace_id == workspace_id,
                SearchDailyMetric.product_id == product_id,
                SearchDailyMetric.source_id == source_id,
                SearchDailyMetric.grain == "site_total",
            )
        )
    ).one()
    return row[0], row[1]


async def source_queries(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, source_id: UUID
) -> dict[UUID, SearchQuery]:
    items = await db.scalars(
        select(SearchQuery).where(
            SearchQuery.workspace_id == workspace_id,
            SearchQuery.product_id == product_id,
            SearchQuery.source_id == source_id,
        )
    )
    return {item.id: item for item in items}


async def source_pages(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, source_id: UUID
) -> dict[UUID, SearchPage]:
    items = await db.scalars(
        select(SearchPage).where(
            SearchPage.workspace_id == workspace_id,
            SearchPage.product_id == product_id,
            SearchPage.source_id == source_id,
        )
    )
    return {item.id: item for item in items}


async def source_runs(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, source_id: UUID
) -> list[SearchSyncRun]:
    return list(
        await db.scalars(
            select(SearchSyncRun)
            .where(
                SearchSyncRun.workspace_id == workspace_id,
                SearchSyncRun.product_id == product_id,
                SearchSyncRun.source_id == source_id,
                SearchSyncRun.deleted_at.is_(None),
            )
            .order_by(SearchSyncRun.created_at.desc(), SearchSyncRun.id.desc())
        )
    )


async def source_context(
    db: AsyncSession, source: SearchSource
) -> tuple[IntegrationConnection | None, SearchProviderProperty | None]:
    connection = await db.get(IntegrationConnection, source.integration_connection_id)
    property_record = await db.get(SearchProviderProperty, source.property_id)
    if connection is not None and connection.workspace_id != source.workspace_id:
        connection = None
    if property_record is not None and property_record.workspace_id != source.workspace_id:
        property_record = None
    return connection, property_record


__all__ = [
    "latest_provider_connection",
    "latest_source",
    "metric_date_bounds",
    "metrics_for_range",
    "source_context",
    "source_pages",
    "source_queries",
    "source_runs",
]
