import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.outbox import OutboxMetadata, enqueue_outbox_event
from app.modules.products.enums.website_intelligence_enum import (
    WebsiteCrawlResultStatus,
    WebsiteSourceStatus,
)
from app.modules.products.models import (
    WebsiteCapabilityMapping,
    WebsiteCrawlRun,
    WebsitePage,
    WebsiteSource,
)
from app.modules.products.policies.product_profile_policy import can_write
from app.modules.products.policies.website_health_policy import (
    CrawlPageObservation,
    WebsiteHealthSummary,
    summarize_website_health,
)
from app.modules.products.policies.website_relevance_policy import RelevancePage, most_relevant_page
from app.modules.products.repositories.product_repository import (
    get_product_for_update,
    get_product_for_workspace,
)
from app.modules.products.repositories.website_intelligence_repository import (
    get_crawl_run,
    get_website_source,
    insert_or_replay_crawl,
    latest_crawl_run,
    list_capability_mappings,
    list_crawl_observations,
    list_crawl_runs,
    list_pages,
)
from app.modules.products.schemas.website_intelligence_schema import (
    WebsiteCrawlRequest,
    WebsiteHealthFailure,
    WebsiteHealthResponse,
    WebsitePageRelevanceResponse,
    WebsitePageResponse,
    WebsiteSourceWrite,
)
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.services.access import require_active_workspace_role
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError

DISCONNECT_ROLES = frozenset({WorkspaceRole.owner, WorkspaceRole.admin})
CRAWL_EVENT_NAME = "website/crawl.requested"


async def _read_access(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID
) -> None:
    if await get_product_for_workspace(db, workspace_id, product_id, user_id) is None:
        raise NotFoundError(
            "Website intelligence not found.", code="website_intelligence_not_found"
        )


async def _write_access(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    *,
    disconnect: bool = False,
) -> None:
    role = await require_active_workspace_role(
        db,
        workspace_id,
        user_id,
        missing=NotFoundError(
            "Website intelligence not found.", code="website_intelligence_not_found"
        ),
    )
    allowed = role in DISCONNECT_ROLES if disconnect else can_write(role)
    if not allowed:
        raise ForbiddenError(
            "You do not have permission to modify website intelligence.",
            code="website_intelligence_forbidden",
        )
    if await get_product_for_update(db, workspace_id, product_id) is None:
        raise NotFoundError(
            "Website intelligence not found.", code="website_intelligence_not_found"
        )


def _same_instant(left: datetime, right: datetime) -> bool:
    def utc_naive(value: datetime) -> datetime:
        return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value

    return utc_naive(left) == utc_naive(right)


async def read_source(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID
) -> WebsiteSource:
    await _read_access(db, workspace_id, product_id, user_id)
    source = await get_website_source(db, workspace_id, product_id)
    if source is None:
        raise NotFoundError("Website source not found.", code="website_source_not_found")
    return source


async def save_source(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    payload: WebsiteSourceWrite,
) -> tuple[WebsiteSource, bool]:
    await _write_access(db, workspace_id, product_id, user_id)
    source = await get_website_source(db, workspace_id, product_id, for_update=True)
    created = source is None
    if source is None:
        if payload.expected_updated_at is not None:
            raise ConflictError(
                "The website source changed. Reload and try again.",
                code="website_source_conflict",
            )
        source = WebsiteSource(
            workspace_id=workspace_id,
            product_id=product_id,
            base_url=payload.base_url,
            normalized_base_url=payload.base_url,
            sitemap_urls=payload.sitemap_urls,
            blog_url=payload.blog_url,
            documentation_url=payload.documentation_url,
            status=WebsiteSourceStatus.active,
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(source)
    else:
        if payload.expected_updated_at is None or not _same_instant(
            payload.expected_updated_at, source.updated_at
        ):
            raise ConflictError(
                "The website source changed. Reload and try again.",
                code="website_source_conflict",
            )
        source.base_url = payload.base_url
        source.normalized_base_url = payload.base_url
        source.sitemap_urls = payload.sitemap_urls
        source.blog_url = payload.blog_url
        source.documentation_url = payload.documentation_url
        source.status = WebsiteSourceStatus.active
        source.disconnected_at = None
        source.updated_by = user_id
        source.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise ConflictError(
            "The website source changed. Reload and try again.",
            code="website_source_conflict",
        ) from error
    except Exception:
        await db.rollback()
        raise
    await db.refresh(source)
    return source, created


async def disconnect_source(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID
) -> None:
    await _write_access(db, workspace_id, product_id, user_id, disconnect=True)
    source = await get_website_source(db, workspace_id, product_id, for_update=True)
    if source is None or source.status == WebsiteSourceStatus.disconnected:
        raise NotFoundError("Website source not found.", code="website_source_not_found")
    now = datetime.now(UTC)
    source.status = WebsiteSourceStatus.disconnected
    source.disconnected_at = now
    source.updated_at = now
    source.updated_by = user_id
    await db.commit()


async def request_crawl(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    idempotency_key: str,
    payload: WebsiteCrawlRequest,
) -> WebsiteCrawlRun:
    await _write_access(db, workspace_id, product_id, user_id)
    key = idempotency_key.strip()
    if not key or len(key) > 255:
        raise ConflictError("Invalid idempotency key.", code="invalid_idempotency_key")
    source = await get_website_source(db, workspace_id, product_id, for_update=True)
    if source is None or source.status != WebsiteSourceStatus.active:
        raise ConflictError("An active website source is required.", code="website_source_inactive")
    try:
        request_fingerprint = hashlib.sha256(
            json.dumps(
                {"pageLimit": payload.page_limit, "trigger": payload.trigger.value},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        result = await insert_or_replay_crawl(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            source_id=source.id,
            trigger=payload.trigger.value,
            idempotency_key=key,
            request_fingerprint=request_fingerprint,
            page_limit=payload.page_limit,
        )
        crawl = result.value
        if not result.inserted and crawl.request_fingerprint != request_fingerprint:
            raise ConflictError(
                "The idempotency key was already used for a different crawl request.",
                code="idempotency_key_reused",
            )
        await enqueue_outbox_event(
            db,
            event_name=CRAWL_EVENT_NAME,
            schema_version=1,
            idempotency_key=str(crawl.id),
            aggregate_id=crawl.id,
            workspace_id=workspace_id,
            product_id=product_id,
            metadata=OutboxMetadata(
                {
                    "workspaceId": str(workspace_id),
                    "productId": str(product_id),
                    "runId": str(crawl.id),
                    "sourceId": str(source.id),
                    "schemaVersion": 1,
                    "correlationId": str(crawl.id),
                }
            ),
        )
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise ConflictError(
            "A website crawl is already pending or running.", code="website_crawl_active"
        ) from error
    except Exception:
        await db.rollback()
        raise
    await db.refresh(crawl)
    return crawl


async def read_crawls(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[WebsiteCrawlRun]:
    await _read_access(db, workspace_id, product_id, user_id)
    return await list_crawl_runs(db, workspace_id, product_id, limit=limit, offset=offset)


async def read_crawl(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID, crawl_run_id: UUID
) -> WebsiteCrawlRun:
    await _read_access(db, workspace_id, product_id, user_id)
    crawl = await get_crawl_run(db, workspace_id, product_id, crawl_run_id)
    if crawl is None:
        raise NotFoundError("Website crawl not found.", code="website_crawl_not_found")
    return crawl


async def read_pages(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[WebsitePage]:
    await _read_access(db, workspace_id, product_id, user_id)
    return await list_pages(db, workspace_id, product_id, limit=limit, offset=offset)


async def read_topic_relevance(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID, topic: str
) -> WebsitePageRelevanceResponse:
    await _read_access(db, workspace_id, product_id, user_id)
    crawl = await latest_crawl_run(db, workspace_id, product_id)
    if crawl is None:
        raise NotFoundError("Website crawl not found.", code="website_crawl_not_found")
    rows = await list_crawl_observations(db, workspace_id, product_id, crawl.id)
    pages = [
        RelevancePage(
            url=page.canonical_url,
            title=result.title or "",
            headings=tuple(
                str(heading.get("text", "")) for heading in result.headings if heading.get("text")
            ),
            text=result.extracted_text or "",
        )
        for page, result in rows
        if result.status == WebsiteCrawlResultStatus.succeeded
    ]
    ranked = most_relevant_page(topic, pages)
    if ranked is None:
        raise NotFoundError("No relevant website page found.", code="website_page_not_relevant")
    page_by_url = {page.canonical_url: page for page, _ in rows}
    return WebsitePageRelevanceResponse(
        page=WebsitePageResponse.model_validate(page_by_url[ranked.page.url]),
        score=ranked.score,
        matched_terms=list(ranked.matched_terms),
    )


async def read_health(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID
) -> WebsiteHealthResponse:
    await _read_access(db, workspace_id, product_id, user_id)
    crawl = await latest_crawl_run(db, workspace_id, product_id)
    if crawl is None:
        summary = WebsiteHealthSummary("unknown", 0, 0, (), ())
    else:
        rows = await list_crawl_observations(db, workspace_id, product_id, crawl.id)
        summary = summarize_website_health(
            CrawlPageObservation(
                url=page.canonical_url,
                status_code=result.http_status,
                error=(result.error_message or result.error_code)
                if result.status
                in {WebsiteCrawlResultStatus.failed, WebsiteCrawlResultStatus.skipped}
                else None,
                blocked_reason=result.error_code
                if result.status == WebsiteCrawlResultStatus.blocked
                else None,
            )
            for page, result in rows
        )
    return WebsiteHealthResponse(
        crawl_run_id=crawl.id if crawl else None,
        status=summary.status,
        total_count=summary.total_count,
        healthy_count=summary.healthy_count,
        blocked_urls=list(summary.blocked_urls),
        failures=[WebsiteHealthFailure(url=url, reason=reason) for url, reason in summary.failures],
    )


async def read_capability_mappings(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    *,
    crawl_run_id: UUID | None,
    limit: int,
    offset: int,
) -> list[WebsiteCapabilityMapping]:
    await _read_access(db, workspace_id, product_id, user_id)
    crawl = (
        await get_crawl_run(db, workspace_id, product_id, crawl_run_id)
        if crawl_run_id
        else await latest_crawl_run(db, workspace_id, product_id)
    )
    if crawl_run_id is not None and crawl is None:
        raise NotFoundError("Website crawl not found.", code="website_crawl_not_found")
    if crawl is None:
        return []
    return await list_capability_mappings(
        db, workspace_id, product_id, crawl.id, limit=limit, offset=offset
    )
