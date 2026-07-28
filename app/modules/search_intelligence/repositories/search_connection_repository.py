from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.products.models import WebsiteSource
from app.modules.search_intelligence.models import (
    SearchProviderProperty,
    SearchSource,
    SearchSyncRun,
)
from app.modules.workspaces.models import Workspace


async def lock_workspace(db: AsyncSession, workspace_id: UUID) -> bool:
    return (
        await db.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
        is not None
    )


async def get_active_website_source(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, *, for_update: bool = False
) -> WebsiteSource | None:
    statement = select(WebsiteSource).where(
        WebsiteSource.workspace_id == workspace_id,
        WebsiteSource.product_id == product_id,
        WebsiteSource.status == "active",
        WebsiteSource.deleted_at.is_(None),
    )
    if for_update:
        statement = statement.with_for_update()
    return (await db.scalars(statement)).first()


async def list_connection_properties(
    db: AsyncSession,
    workspace_id: UUID,
    connection_id: UUID,
    *,
    include_retired: bool = False,
) -> list[SearchProviderProperty]:
    conditions = [
        SearchProviderProperty.workspace_id == workspace_id,
        SearchProviderProperty.integration_connection_id == connection_id,
    ]
    if not include_retired:
        conditions.append(SearchProviderProperty.deleted_at.is_(None))
    return list(
        await db.scalars(
            select(SearchProviderProperty)
            .where(*conditions)
            .order_by(
                SearchProviderProperty.first_discovered_at.asc(),
                SearchProviderProperty.id.asc(),
            )
        )
    )


async def get_connection_property(
    db: AsyncSession,
    workspace_id: UUID,
    connection_id: UUID,
    property_id: UUID,
) -> SearchProviderProperty | None:
    return (
        await db.scalars(
            select(SearchProviderProperty).where(
                SearchProviderProperty.workspace_id == workspace_id,
                SearchProviderProperty.integration_connection_id == connection_id,
                SearchProviderProperty.id == property_id,
                SearchProviderProperty.deleted_at.is_(None),
            )
        )
    ).first()


async def get_active_search_source(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, *, for_update: bool = False
) -> SearchSource | None:
    statement = select(SearchSource).where(
        SearchSource.workspace_id == workspace_id,
        SearchSource.product_id == product_id,
        SearchSource.status == "active",
        SearchSource.deleted_at.is_(None),
    )
    if for_update:
        statement = statement.with_for_update()
    return (await db.scalars(statement)).first()


async def disconnect_source(source: SearchSource, actor_id: UUID, now: datetime) -> None:
    source.status = "disconnected"
    source.disconnected_at = now
    source.updated_at = now
    source.updated_by = actor_id


async def disconnect_connection_sources(
    db: AsyncSession, workspace_id: UUID, connection_id: UUID, actor_id: UUID, now: datetime
) -> list[UUID]:
    source_ids = list(
        await db.scalars(
            select(SearchSource.id).where(
                SearchSource.workspace_id == workspace_id,
                SearchSource.integration_connection_id == connection_id,
                SearchSource.status == "active",
                SearchSource.deleted_at.is_(None),
            )
        )
    )
    if source_ids:
        await db.execute(
            update(SearchSource)
            .where(
                SearchSource.workspace_id == workspace_id,
                SearchSource.id.in_(source_ids),
            )
            .values(
                status="disconnected",
                disconnected_at=now,
                updated_at=now,
                updated_by=actor_id,
            )
        )
    return source_ids


async def cancel_active_runs(
    db: AsyncSession, workspace_id: UUID, source_ids: list[UUID], now: datetime
) -> None:
    if not source_ids:
        return
    await db.execute(
        update(SearchSyncRun)
        .where(
            SearchSyncRun.workspace_id == workspace_id,
            SearchSyncRun.source_id.in_(source_ids),
            SearchSyncRun.status.in_(("pending", "running")),
        )
        .values(
            status="cancelled",
            completed_at=now,
            lease_owner=None,
            lease_expires_at=None,
            failure_category=None,
            updated_at=now,
        )
    )


__all__ = [
    "cancel_active_runs",
    "disconnect_connection_sources",
    "disconnect_source",
    "get_active_search_source",
    "get_active_website_source",
    "get_connection_property",
    "list_connection_properties",
    "lock_workspace",
]
