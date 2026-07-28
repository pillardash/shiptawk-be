from datetime import date, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Row, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.integrations.models import IntegrationConnection, normalized_events
from app.modules.operator.models import Opportunity, OpportunityFeedback, event_evaluations
from app.modules.products.models import (
    Product,
    ProductProfile,
    WebsiteCapabilityMapping,
    WebsiteCrawlResult,
    WebsiteCrawlRun,
    WebsitePage,
    WebsiteSource,
    product_repositories,
)
from app.modules.search_intelligence.models import (
    SearchDailyMetric,
    SearchPage,
    SearchQuery,
    SearchSource,
    SearchSyncRun,
)


async def get_product_and_profile(
    db: AsyncSession, workspace_id: UUID, product_id: UUID
) -> tuple[Product | None, ProductProfile | None]:
    product = await db.scalar(
        select(Product).where(Product.workspace_id == workspace_id, Product.id == product_id)
    )
    profile = await db.scalar(
        select(ProductProfile).where(
            ProductProfile.workspace_id == workspace_id,
            ProductProfile.product_id == product_id,
            ProductProfile.status == "approved",
            ProductProfile.deleted_at.is_(None),
        )
    )
    return product, profile


async def get_search_authority(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    as_of: datetime,
    required_start: date | None = None,
    required_end: date | None = None,
) -> tuple[SearchSource, SearchSyncRun] | None:
    coverage: tuple[ColumnElement[bool], ...] = ()
    if required_start is not None and required_end is not None:
        coverage = (
            SearchSyncRun.requested_start_date <= required_start,
            SearchSyncRun.requested_end_date >= required_end,
        )
    row = (
        await db.execute(
            select(SearchSource, SearchSyncRun)
            .join(
                IntegrationConnection,
                and_(
                    IntegrationConnection.workspace_id == SearchSource.workspace_id,
                    IntegrationConnection.id == SearchSource.integration_connection_id,
                ),
            )
            .join(
                SearchSyncRun,
                and_(
                    SearchSyncRun.workspace_id == SearchSource.workspace_id,
                    SearchSyncRun.product_id == SearchSource.product_id,
                    SearchSyncRun.source_id == SearchSource.id,
                ),
            )
            .where(
                SearchSource.workspace_id == workspace_id,
                SearchSource.product_id == product_id,
                SearchSource.status == "active",
                SearchSource.deleted_at.is_(None),
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.status == "active",
                IntegrationConnection.deleted_at.is_(None),
                SearchSyncRun.workspace_id == workspace_id,
                SearchSyncRun.product_id == product_id,
                SearchSyncRun.status == "succeeded",
                SearchSyncRun.is_complete.is_(True),
                SearchSyncRun.deleted_at.is_(None),
                SearchSyncRun.completed_at <= as_of,
                *coverage,
            )
            .order_by(SearchSyncRun.completed_at.desc(), SearchSyncRun.id.desc())
            .limit(1)
        )
    ).first()
    return cast(tuple[SearchSource, SearchSyncRun] | None, row)


async def list_search_facts(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    source_id: UUID,
    start: date,
    end: date,
    as_of: datetime,
) -> tuple[list[SearchDailyMetric], list[SearchQuery], list[SearchPage], list[WebsitePage], bool]:
    metrics = list(
        await db.scalars(
            select(SearchDailyMetric).where(
                SearchDailyMetric.workspace_id == workspace_id,
                SearchDailyMetric.product_id == product_id,
                SearchDailyMetric.source_id == source_id,
                SearchDailyMetric.metric_date.between(start, end),
                SearchDailyMetric.deleted_at.is_(None),
            )
        )
    )
    queries = list(
        await db.scalars(
            select(SearchQuery).where(
                SearchQuery.workspace_id == workspace_id,
                SearchQuery.product_id == product_id,
                SearchQuery.source_id == source_id,
                SearchQuery.deleted_at.is_(None),
            )
        )
    )
    pages = list(
        await db.scalars(
            select(SearchPage).where(
                SearchPage.workspace_id == workspace_id,
                SearchPage.product_id == product_id,
                SearchPage.source_id == source_id,
                SearchPage.deleted_at.is_(None),
            )
        )
    )
    website_page_ids = [page.website_page_id for page in pages if page.website_page_id is not None]
    website_pages = list(
        await db.scalars(
            select(WebsitePage).where(
                WebsitePage.workspace_id == workspace_id,
                WebsitePage.product_id == product_id,
                WebsitePage.id.in_(website_page_ids),
                WebsitePage.deleted_at.is_(None),
            )
        )
    )
    truncated = bool(
        await db.scalar(
            select(SearchSyncRun.id).where(
                SearchSyncRun.workspace_id == workspace_id,
                SearchSyncRun.product_id == product_id,
                SearchSyncRun.source_id == source_id,
                SearchSyncRun.status == "succeeded",
                SearchSyncRun.requested_start_date <= end,
                SearchSyncRun.requested_end_date >= start,
                SearchSyncRun.is_truncated.is_(True),
                SearchSyncRun.completed_at <= as_of,
                SearchSyncRun.deleted_at.is_(None),
            )
        )
    )
    return metrics, queries, pages, website_pages, truncated


async def get_website_authority(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, as_of: datetime
) -> tuple[WebsiteSource, WebsiteCrawlRun, bool] | None:
    row = (
        await db.execute(
            select(WebsiteSource, WebsiteCrawlRun)
            .join(
                WebsiteCrawlRun,
                and_(
                    WebsiteCrawlRun.workspace_id == WebsiteSource.workspace_id,
                    WebsiteCrawlRun.product_id == WebsiteSource.product_id,
                    WebsiteCrawlRun.source_id == WebsiteSource.id,
                ),
            )
            .where(
                WebsiteSource.workspace_id == workspace_id,
                WebsiteSource.product_id == product_id,
                WebsiteSource.status == "active",
                WebsiteSource.deleted_at.is_(None),
                WebsiteCrawlRun.workspace_id == workspace_id,
                WebsiteCrawlRun.product_id == product_id,
                WebsiteCrawlRun.status == "succeeded",
                WebsiteCrawlRun.completed_at <= as_of,
                WebsiteCrawlRun.deleted_at.is_(None),
            )
            .order_by(WebsiteCrawlRun.completed_at.desc(), WebsiteCrawlRun.id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    source, run = cast(tuple[WebsiteSource, WebsiteCrawlRun], row)
    newer_failed = bool(
        await db.scalar(
            select(WebsiteCrawlRun.id).where(
                WebsiteCrawlRun.workspace_id == workspace_id,
                WebsiteCrawlRun.product_id == product_id,
                WebsiteCrawlRun.source_id == source.id,
                WebsiteCrawlRun.status.in_(("failed", "cancelled")),
                WebsiteCrawlRun.completed_at <= as_of,
                WebsiteCrawlRun.completed_at > run.completed_at,
                WebsiteCrawlRun.deleted_at.is_(None),
            )
        )
    )
    return source, run, newer_failed


async def list_website_facts(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, source_id: UUID, crawl_id: UUID
) -> tuple[list[tuple[WebsiteCrawlResult, WebsitePage]], list[WebsiteCapabilityMapping]]:
    results = list(
        (
            await db.execute(
                select(WebsiteCrawlResult, WebsitePage)
                .join(
                    WebsitePage,
                    and_(
                        WebsitePage.workspace_id == WebsiteCrawlResult.workspace_id,
                        WebsitePage.product_id == WebsiteCrawlResult.product_id,
                        WebsitePage.id == WebsiteCrawlResult.page_id,
                    ),
                )
                .where(
                    WebsiteCrawlResult.workspace_id == workspace_id,
                    WebsiteCrawlResult.product_id == product_id,
                    WebsiteCrawlResult.source_id == source_id,
                    WebsiteCrawlResult.crawl_run_id == crawl_id,
                    WebsiteCrawlResult.deleted_at.is_(None),
                    WebsitePage.workspace_id == workspace_id,
                    WebsitePage.product_id == product_id,
                    WebsitePage.deleted_at.is_(None),
                )
            )
        ).tuples()
    )
    mappings = list(
        await db.scalars(
            select(WebsiteCapabilityMapping).where(
                WebsiteCapabilityMapping.workspace_id == workspace_id,
                WebsiteCapabilityMapping.product_id == product_id,
                WebsiteCapabilityMapping.source_id == source_id,
                WebsiteCapabilityMapping.crawl_run_id == crawl_id,
                WebsiteCapabilityMapping.deleted_at.is_(None),
            )
        )
    )
    return results, mappings


async def list_shipping_rows(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, start: datetime, end: datetime
) -> list[Row[tuple[object, ...]]]:
    statement = (
        select(
            normalized_events.c.id,
            normalized_events.c.dedupe_key,
            normalized_events.c.repo_id,
            normalized_events.c.event_type,
            normalized_events.c.occurred_at,
            product_repositories.c.id.label("mapping_id"),
            event_evaluations.c.outcome,
            event_evaluations.c.public_worthiness_score,
            event_evaluations.c.public_summary,
            event_evaluations.c.user_benefit,
        )
        .join(
            product_repositories,
            and_(
                product_repositories.c.workspace_id == normalized_events.c.workspace_id,
                product_repositories.c.repo_id == normalized_events.c.repo_id,
            ),
        )
        .join(
            event_evaluations,
            and_(
                event_evaluations.c.workspace_id == normalized_events.c.workspace_id,
                event_evaluations.c.normalized_event_id == normalized_events.c.id,
            ),
        )
        .where(
            normalized_events.c.workspace_id == workspace_id,
            product_repositories.c.workspace_id == workspace_id,
            product_repositories.c.product_id == product_id,
            event_evaluations.c.workspace_id == workspace_id,
            normalized_events.c.occurred_at.between(start, end),
        )
        .order_by(normalized_events.c.occurred_at, normalized_events.c.id)
    )
    return list((await db.execute(statement)).all())


async def list_history(
    db: AsyncSession, workspace_id: UUID, product_id: UUID
) -> list[tuple[Opportunity, OpportunityFeedback | None]]:
    latest_feedback = (
        select(OpportunityFeedback.id)
        .where(
            OpportunityFeedback.workspace_id == workspace_id,
            OpportunityFeedback.product_id == product_id,
            OpportunityFeedback.opportunity_id == Opportunity.id,
        )
        .order_by(OpportunityFeedback.created_at.desc(), OpportunityFeedback.id.desc())
        .limit(1)
        .correlate(Opportunity)
        .scalar_subquery()
    )
    return list(
        (
            await db.execute(
                select(Opportunity, OpportunityFeedback)
                .outerjoin(OpportunityFeedback, OpportunityFeedback.id == latest_feedback)
                .where(
                    Opportunity.workspace_id == workspace_id,
                    Opportunity.product_id == product_id,
                )
                .order_by(Opportunity.created_at, Opportunity.id)
            )
        ).tuples()
    )
