from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db.base import Base
from app.db.outbox import OutboxEvent
from app.db.session import get_db
from app.main import create_app
from app.modules.identity.models.users import User
from app.modules.products.enums.website_intelligence_enum import (
    WebsiteCapabilityMappingStatus,
    WebsiteCrawlResultStatus,
    WebsiteCrawlRunStatus,
    WebsiteCrawlTrigger,
    WebsitePageStatus,
)
from app.modules.products.models import (
    Product,
    WebsiteCapabilityMapping,
    WebsiteCrawlResult,
    WebsiteCrawlRun,
    WebsitePage,
    WebsiteSource,
)
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import Workspace, WorkspaceMembership


@pytest.fixture
def website_client() -> Generator[tuple[TestClient, async_sessionmaker[AsyncSession]], None, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, sessions
        client.portal.call(engine.dispose)


async def seed_product(
    sessions: async_sessionmaker[AsyncSession], role: WorkspaceRole = WorkspaceRole.editor
) -> tuple[User, Workspace, Workspace, Product]:
    async with sessions() as db:
        user = User(email=f"website-{role.value}-{uuid4()}@example.com")
        workspace = Workspace(name="Website workspace")
        other = Workspace(name="Other workspace")
        db.add_all([user, workspace, other])
        await db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=user.id, role=role, is_active=True
            )
        )
        product = Product(workspace_id=workspace.id, user_id=user.id, name="Website product")
        db.add(product)
        await db.commit()
        return user, workspace, other, product


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def source_url(workspace: Workspace, product: Product) -> str:
    return f"/api/v1/workspaces/{workspace.id}/products/{product.id}/website"


def test_source_is_singleton_normalized_optimistic_and_disconnectable(
    website_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = website_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_product, sessions, WorkspaceRole.owner)
    url = source_url(workspace, product)

    missing = client.get(url, headers=auth(user))
    created = client.post(
        url,
        headers=auth(user),
        json={
            "baseUrl": "HTTPS://Example.COM:443//product/../",
            "sitemapUrls": ["https://example.com/sitemap.xml", "https://example.com/sitemap.xml"],
        },
    )
    stale = client.post(
        url,
        headers=auth(user),
        json={"baseUrl": "https://example.com/new", "expectedUpdatedAt": "2020-01-01T00:00:00Z"},
    )
    updated = client.post(
        url,
        headers=auth(user),
        json={
            "baseUrl": "https://example.com/new",
            "expectedUpdatedAt": created.json()["updatedAt"],
        },
    )
    disconnected = client.delete(url, headers=auth(user))
    read_disconnected = client.get(url, headers=auth(user))

    assert missing.status_code == 404
    assert created.status_code == 201
    assert created.json()["normalizedBaseUrl"] == "https://example.com/"
    assert created.json()["sitemapUrls"] == ["https://example.com/sitemap.xml"]
    assert stale.status_code == 409
    assert updated.status_code == 200
    assert disconnected.status_code == 204
    assert read_disconnected.json()["status"] == "disconnected"


@pytest.mark.parametrize(
    "unsafe_url", ["http://127.0.0.1", "https://localhost/admin", "ftp://example.com/file"]
)
def test_source_rejects_unsafe_urls(
    website_client: tuple[TestClient, async_sessionmaker[AsyncSession]], unsafe_url: str
) -> None:
    client, sessions = website_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_product, sessions)

    response = client.post(
        source_url(workspace, product), headers=auth(user), json={"baseUrl": unsafe_url}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("role", [WorkspaceRole.reviewer, WorkspaceRole.viewer])
def test_read_only_roles_can_read_but_cannot_configure_or_crawl(
    website_client: tuple[TestClient, async_sessionmaker[AsyncSession]], role: WorkspaceRole
) -> None:
    client, sessions = website_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_product, sessions, role)

    read = client.get(source_url(workspace, product), headers=auth(user))
    write = client.post(
        source_url(workspace, product), headers=auth(user), json={"baseUrl": "https://example.com"}
    )
    crawl = client.post(
        f"/api/v1/workspaces/{workspace.id}/products/{product.id}/website/crawl",
        headers={**auth(user), "Idempotency-Key": "readonly-crawl"},
        json={},
    )

    assert read.status_code == 404
    assert write.status_code == 403
    assert crawl.status_code == 403


def test_only_owner_and_admin_can_disconnect_and_cross_workspace_is_hidden(
    website_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = website_client
    assert client.portal is not None
    user, workspace, other, product = client.portal.call(seed_product, sessions)
    client.post(
        source_url(workspace, product), headers=auth(user), json={"baseUrl": "https://example.com"}
    )

    forbidden = client.delete(source_url(workspace, product), headers=auth(user))
    hidden = client.get(source_url(other, product), headers=auth(user))

    assert forbidden.status_code == 403
    assert hidden.status_code == 404


def test_crawl_requires_key_and_replays_one_run_and_one_metadata_only_event(
    website_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = website_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_product, sessions)
    client.post(
        source_url(workspace, product), headers=auth(user), json={"baseUrl": "https://example.com"}
    )
    url = f"/api/v1/workspaces/{workspace.id}/products/{product.id}/website/crawl"

    missing_key = client.post(url, headers=auth(user), json={})
    first = client.post(
        url, headers={**auth(user), "Idempotency-Key": "crawl-once"}, json={"pageLimit": 10}
    )
    replay = client.post(
        url, headers={**auth(user), "Idempotency-Key": "crawl-once"}, json={"pageLimit": 10}
    )
    mismatched_replay = client.post(
        url, headers={**auth(user), "Idempotency-Key": "crawl-once"}, json={"pageLimit": 11}
    )
    competing = client.post(
        url, headers={**auth(user), "Idempotency-Key": "another-crawl"}, json={}
    )

    async def counts() -> tuple[int, list[OutboxEvent]]:
        async with sessions() as db:
            crawl_count = await db.scalar(select(func.count()).select_from(WebsiteCrawlRun))
            events = list(await db.scalars(select(OutboxEvent)))
            return int(crawl_count or 0), events

    crawl_count, events = client.portal.call(counts)
    assert missing_key.status_code == 422
    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    assert mismatched_replay.status_code == 409
    assert mismatched_replay.json()["code"] == "idempotency_key_reused"
    assert competing.status_code == 409
    assert competing.json()["code"] == "website_crawl_active"
    assert crawl_count == 1
    assert len(events) == 1
    assert events[0].event_name == "website/crawl.requested"
    assert events[0].metadata_payload == {
        "workspaceId": str(workspace.id),
        "productId": str(product.id),
        "runId": first.json()["id"],
        "sourceId": first.json()["sourceId"],
        "schemaVersion": 1,
        "correlationId": first.json()["id"],
    }


def test_pages_health_and_capability_mappings_use_latest_crawl_results(
    website_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = website_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_product, sessions, WorkspaceRole.viewer)

    async def seed_inventory() -> WebsiteCrawlRun:
        async with sessions() as db:
            source = WebsiteSource(
                workspace_id=workspace.id,
                product_id=product.id,
                base_url="https://example.com",
                normalized_base_url="https://example.com/",
                sitemap_urls=[],
            )
            db.add(source)
            await db.flush()
            run = WebsiteCrawlRun(
                workspace_id=workspace.id,
                product_id=product.id,
                source_id=source.id,
                status=WebsiteCrawlRunStatus.succeeded,
                trigger=WebsiteCrawlTrigger.manual,
                idempotency_key="completed",
                request_fingerprint="0" * 64,
                page_limit=10,
                pages_discovered=2,
                pages_succeeded=1,
                pages_failed=1,
                summary={},
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
            db.add(run)
            await db.flush()
            healthy = WebsitePage(
                workspace_id=workspace.id,
                product_id=product.id,
                source_id=source.id,
                first_discovered_run_id=run.id,
                url="https://example.com/",
                canonical_url="https://example.com/",
                url_fingerprint="a" * 64,
                status=WebsitePageStatus.active,
            )
            blocked = WebsitePage(
                workspace_id=workspace.id,
                product_id=product.id,
                source_id=source.id,
                first_discovered_run_id=run.id,
                url="https://example.com/private",
                canonical_url="https://example.com/private",
                url_fingerprint="b" * 64,
                status=WebsitePageStatus.blocked,
            )
            db.add_all([healthy, blocked])
            await db.flush()
            db.add_all(
                [
                    WebsiteCrawlResult(
                        workspace_id=workspace.id,
                        product_id=product.id,
                        source_id=source.id,
                        crawl_run_id=run.id,
                        page_id=healthy.id,
                        status=WebsiteCrawlResultStatus.succeeded,
                        final_url=healthy.url,
                        http_status=200,
                        title="Marketing automation",
                        headings=[{"text": "Automate product marketing"}],
                        extracted_text=(
                            "Turn product updates into evidence-backed marketing actions."
                        ),
                        content_fingerprint="c" * 64,
                        fetched_at=datetime.now(UTC),
                    ),
                    WebsiteCrawlResult(
                        workspace_id=workspace.id,
                        product_id=product.id,
                        source_id=source.id,
                        crawl_run_id=run.id,
                        page_id=blocked.id,
                        status=WebsiteCrawlResultStatus.blocked,
                        final_url=blocked.url,
                        http_status=403,
                        error_code="robots_blocked",
                        fetched_at=datetime.now(UTC),
                    ),
                    WebsiteCapabilityMapping(
                        workspace_id=workspace.id,
                        product_id=product.id,
                        source_id=source.id,
                        crawl_run_id=run.id,
                        page_id=healthy.id,
                        capability_key="automation",
                        capability_name="Automation",
                        status=WebsiteCapabilityMappingStatus.represented,
                        relevance_score=90,
                        confidence_score=85,
                        evidence={"heading": "Automation"},
                        mapping_version="v1",
                    ),
                ]
            )
            await db.commit()
            return run

    run = client.portal.call(seed_inventory)
    root = f"/api/v1/workspaces/{workspace.id}/products/{product.id}"
    pages = client.get(f"{root}/website/pages?limit=1", headers=auth(user))
    health = client.get(f"{root}/website/health", headers=auth(user))
    mappings = client.get(f"{root}/website/capability-mappings", headers=auth(user))
    detail = client.get(f"{root}/website/crawls/{run.id}", headers=auth(user))
    relevance = client.get(
        f"{root}/website/relevance", params={"topic": "marketing automation"}, headers=auth(user)
    )

    assert (
        pages.status_code
        == health.status_code
        == mappings.status_code
        == detail.status_code
        == relevance.status_code
        == 200
    )
    assert len(pages.json()) == 1
    assert health.json()["status"] == "degraded"
    assert health.json()["blockedUrls"] == ["https://example.com/private"]
    assert mappings.json()[0]["capabilityKey"] == "automation"
    assert relevance.json()["page"]["canonicalUrl"] == "https://example.com/"
    assert relevance.json()["score"] > 0
