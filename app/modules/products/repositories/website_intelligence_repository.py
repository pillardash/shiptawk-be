from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.idempotency import InsertReplayResult, insert_or_replay
from app.modules.products.enums.website_intelligence_enum import WebsiteCrawlRunStatus
from app.modules.products.models import (
    WebsiteCapabilityMapping,
    WebsiteCrawlResult,
    WebsiteCrawlRun,
    WebsitePage,
    WebsiteSource,
)


async def get_website_source(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, *, for_update: bool = False
) -> WebsiteSource | None:
    statement = select(WebsiteSource).where(
        WebsiteSource.workspace_id == workspace_id,
        WebsiteSource.product_id == product_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return (await db.scalars(statement)).first()


async def insert_or_replay_crawl(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    source_id: UUID,
    trigger: str,
    idempotency_key: str,
    request_fingerprint: str,
    page_limit: int,
) -> InsertReplayResult[WebsiteCrawlRun]:
    result = await insert_or_replay(
        db,
        cast(Table, WebsiteCrawlRun.__table__),
        values={
            "id": uuid4(),
            "workspace_id": workspace_id,
            "product_id": product_id,
            "source_id": source_id,
            "status": WebsiteCrawlRunStatus.pending,
            "trigger": trigger,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "schema_version": 1,
            "page_limit": page_limit,
            "pages_discovered": 0,
            "pages_succeeded": 0,
            "pages_failed": 0,
            "summary": {},
        },
        conflict_columns=("workspace_id", "source_id", "idempotency_key"),
    )
    crawl = await db.get(WebsiteCrawlRun, result.value["id"], populate_existing=True)
    if crawl is None:
        raise RuntimeError("crawl insert succeeded without a readable run")
    return InsertReplayResult(value=crawl, inserted=result.inserted)


async def list_crawl_runs(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, *, limit: int, offset: int
) -> list[WebsiteCrawlRun]:
    return list(
        await db.scalars(
            select(WebsiteCrawlRun)
            .where(
                WebsiteCrawlRun.workspace_id == workspace_id,
                WebsiteCrawlRun.product_id == product_id,
            )
            .order_by(WebsiteCrawlRun.created_at.desc(), WebsiteCrawlRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )


async def get_crawl_run(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, crawl_run_id: UUID
) -> WebsiteCrawlRun | None:
    return (
        await db.scalars(
            select(WebsiteCrawlRun).where(
                WebsiteCrawlRun.workspace_id == workspace_id,
                WebsiteCrawlRun.product_id == product_id,
                WebsiteCrawlRun.id == crawl_run_id,
            )
        )
    ).first()


async def latest_crawl_run(
    db: AsyncSession, workspace_id: UUID, product_id: UUID
) -> WebsiteCrawlRun | None:
    return (
        await db.scalars(
            select(WebsiteCrawlRun)
            .where(
                WebsiteCrawlRun.workspace_id == workspace_id,
                WebsiteCrawlRun.product_id == product_id,
                WebsiteCrawlRun.completed_at.is_not(None),
            )
            .order_by(WebsiteCrawlRun.created_at.desc(), WebsiteCrawlRun.id.desc())
            .limit(1)
        )
    ).first()


async def list_pages(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, *, limit: int, offset: int
) -> list[WebsitePage]:
    return list(
        await db.scalars(
            select(WebsitePage)
            .where(
                WebsitePage.workspace_id == workspace_id,
                WebsitePage.product_id == product_id,
            )
            .order_by(WebsitePage.canonical_url.asc(), WebsitePage.id.asc())
            .limit(limit)
            .offset(offset)
        )
    )


async def list_crawl_observations(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, crawl_run_id: UUID
) -> list[tuple[WebsitePage, WebsiteCrawlResult]]:
    rows = await db.execute(
        select(WebsitePage, WebsiteCrawlResult)
        .join(
            WebsiteCrawlResult,
            (WebsiteCrawlResult.workspace_id == WebsitePage.workspace_id)
            & (WebsiteCrawlResult.product_id == WebsitePage.product_id)
            & (WebsiteCrawlResult.page_id == WebsitePage.id),
        )
        .where(
            WebsitePage.workspace_id == workspace_id,
            WebsitePage.product_id == product_id,
            WebsiteCrawlResult.crawl_run_id == crawl_run_id,
        )
        .order_by(WebsitePage.canonical_url.asc(), WebsitePage.id.asc())
    )
    return [(page, result) for page, result in rows.tuples()]


async def list_capability_mappings(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    crawl_run_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[WebsiteCapabilityMapping]:
    return list(
        await db.scalars(
            select(WebsiteCapabilityMapping)
            .where(
                WebsiteCapabilityMapping.workspace_id == workspace_id,
                WebsiteCapabilityMapping.product_id == product_id,
                WebsiteCapabilityMapping.crawl_run_id == crawl_run_id,
            )
            .order_by(
                WebsiteCapabilityMapping.capability_key.asc(),
                WebsiteCapabilityMapping.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
