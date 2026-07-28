from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.products.enums.product_profile_enum import ProductProfileStatus
from app.modules.products.enums.website_intelligence_enum import (
    WebsiteCrawlRunStatus,
    WebsiteCrawlTrigger,
    WebsiteSourceStatus,
)
from app.modules.products.models import (
    ProductProfile,
    WebsiteCapabilityMapping,
    WebsiteCrawlResult,
    WebsiteCrawlRun,
    WebsitePage,
    WebsiteSource,
)
from app.modules.products.providers.website_fake_provider import FakeWebsiteFetchProvider
from app.modules.products.providers.website_fetch_provider import (
    WebsiteFetchResult,
    WebsiteFetchTransientError,
)
from app.modules.products.services.website_crawl_service import WebsiteCrawlService


@pytest.fixture
async def crawl_database() -> AsyncGenerator[
    tuple[async_sessionmaker[AsyncSession], UUID, UUID, UUID], None
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, product_id, source_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with sessions() as db:
        db.add(
            ProductProfile(
                workspace_id=workspace_id,
                product_id=product_id,
                status=ProductProfileStatus.approved,
                version=1,
                draft_revision=1,
                schema_version=1,
                important_capabilities=["Product features"],
                restricted_topics=[],
                restricted_claims=[],
                restricted_language=[],
                completeness_score=100,
                approved_at=datetime.now(UTC),
                approved_by=uuid4(),
            )
        )
        db.add(
            WebsiteSource(
                id=source_id,
                workspace_id=workspace_id,
                product_id=product_id,
                base_url="https://example.com",
                normalized_base_url="https://example.com/",
                status=WebsiteSourceStatus.active,
                sitemap_urls=["https://example.com/sitemap.xml"],
            )
        )
        db.add(
            WebsiteCrawlRun(
                id=run_id,
                workspace_id=workspace_id,
                product_id=product_id,
                source_id=source_id,
                status=WebsiteCrawlRunStatus.pending,
                trigger=WebsiteCrawlTrigger.manual,
                idempotency_key="crawl-1",
                request_fingerprint="0" * 64,
                page_limit=3,
            )
        )
        await db.commit()
    yield sessions, source_id, run_id, workspace_id
    await engine.dispose()


def response(url: str, body: str, content_type: str = "text/html") -> WebsiteFetchResult:
    return WebsiteFetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        body=body.encode(),
    )


def test_crawl_scope_allows_www_equivalent_but_rejects_other_public_sites() -> None:
    assert WebsiteCrawlService._same_site(
        "https://example.com/", "https://www.example.com/features"
    )
    assert not WebsiteCrawlService._same_site(
        "https://example.com/", "https://other.example/features"
    )


@pytest.mark.asyncio
async def test_crawl_service_discovers_sitemap_and_links_and_replays_idempotently(
    crawl_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, UUID],
) -> None:
    sessions, source_id, run_id, _ = crawl_database
    provider = FakeWebsiteFetchProvider(
        {
            "https://example.com/": response(
                "https://example.com/",
                '<title>Home</title><h1>Home</h1><a href="/pricing">Pricing</a>',
            ),
            "https://example.com/sitemap.xml": response(
                "https://example.com/sitemap.xml",
                "<urlset><url><loc>https://example.com/features</loc></url></urlset>",
                "application/xml",
            ),
            "https://example.com/pricing": response(
                "https://example.com/pricing", "<title>Pricing</title><h1>Plans</h1>"
            ),
            "https://example.com/features": response(
                "https://example.com/features", "<title>Features</title><h1>Features</h1>"
            ),
        }
    )
    service = WebsiteCrawlService(sessions=sessions, provider=provider)

    first = await service.process(run_id)
    second = await service.process(run_id)

    assert first == second == {"status": "succeeded", "pagesSucceeded": 2, "pagesFailed": 0}
    assert provider.calls == [
        "https://example.com/robots.txt",
        "https://example.com/",
        "https://example.com/sitemap.xml",
        "https://example.com/features",
    ]
    async with sessions() as db:
        run = await db.get(WebsiteCrawlRun, run_id)
        source = await db.get(WebsiteSource, source_id)
        pages = list(await db.scalars(select(WebsitePage).order_by(WebsitePage.url)))
        results = list(await db.scalars(select(WebsiteCrawlResult)))
        mappings = list(await db.scalars(select(WebsiteCapabilityMapping)))
    assert run is not None and run.status == WebsiteCrawlRunStatus.succeeded
    assert run.pages_discovered == run.pages_succeeded == 2
    assert source is not None and source.last_successful_crawl_at is not None
    assert [page.url for page in pages] == ["https://example.com/", "https://example.com/features"]
    assert len(results) == 2
    assert len(mappings) == 1
    assert mappings[0].capability_name == "Product features"
    assert mappings[0].page_id is not None


@pytest.mark.asyncio
async def test_crawl_service_finalizes_base_fetch_failure(
    crawl_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID, UUID],
) -> None:
    sessions, _, run_id, _ = crawl_database
    provider = FakeWebsiteFetchProvider(
        {"https://example.com/": WebsiteFetchTransientError("dns_failure")}
    )

    result = await WebsiteCrawlService(sessions=sessions, provider=provider).process(run_id)

    assert result == {"status": "failed", "errorCode": "dns_failure"}
    async with sessions() as db:
        run = await db.get(WebsiteCrawlRun, run_id)
    assert run is not None
    assert run.status == WebsiteCrawlRunStatus.failed
    assert run.started_at is not None and run.completed_at is not None
