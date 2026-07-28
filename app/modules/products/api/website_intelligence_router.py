from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.deps import DbDep, MutationUserDep, get_current_user
from app.modules.identity.models.users import User
from app.modules.products.schemas.website_intelligence_schema import (
    WebsiteCapabilityMappingResponse,
    WebsiteCrawlRequest,
    WebsiteCrawlRunResponse,
    WebsiteHealthResponse,
    WebsitePageRelevanceResponse,
    WebsitePageResponse,
    WebsiteSourceResponse,
    WebsiteSourceWrite,
)
from app.modules.products.services.website_intelligence_service import (
    disconnect_source,
    read_capability_mappings,
    read_crawl,
    read_crawls,
    read_health,
    read_pages,
    read_source,
    read_topic_relevance,
    request_crawl,
    save_source,
)

website_intelligence_router = APIRouter(tags=["website-intelligence"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=10000)]


@website_intelligence_router.get("/{product_id}/website", response_model=WebsiteSourceResponse)
async def get_website_source_endpoint(
    workspace_id: UUID, product_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> WebsiteSourceResponse:
    return WebsiteSourceResponse.model_validate(
        await read_source(db, workspace_id, product_id, current_user.id)
    )


@website_intelligence_router.post("/{product_id}/website", response_model=WebsiteSourceResponse)
async def save_website_source_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    payload: WebsiteSourceWrite,
    response: Response,
    current_user: MutationUserDep,
    db: DbDep,
) -> WebsiteSourceResponse:
    source, created = await save_source(db, workspace_id, product_id, current_user.id, payload)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return WebsiteSourceResponse.model_validate(source)


@website_intelligence_router.delete("/{product_id}/website", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_website_source_endpoint(
    workspace_id: UUID, product_id: UUID, current_user: MutationUserDep, db: DbDep
) -> Response:
    await disconnect_source(db, workspace_id, product_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@website_intelligence_router.post(
    "/{product_id}/website/crawl",
    response_model=WebsiteCrawlRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_website_crawl_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    payload: WebsiteCrawlRequest,
    current_user: MutationUserDep,
    db: DbDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> WebsiteCrawlRunResponse:
    return WebsiteCrawlRunResponse.model_validate(
        await request_crawl(db, workspace_id, product_id, current_user.id, idempotency_key, payload)
    )


@website_intelligence_router.get(
    "/{product_id}/website/crawls", response_model=list[WebsiteCrawlRunResponse]
)
async def list_website_crawls_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[WebsiteCrawlRunResponse]:
    return [
        WebsiteCrawlRunResponse.model_validate(row)
        for row in await read_crawls(
            db, workspace_id, product_id, current_user.id, limit=limit, offset=offset
        )
    ]


@website_intelligence_router.get(
    "/{product_id}/website/crawls/{crawl_run_id}", response_model=WebsiteCrawlRunResponse
)
async def get_website_crawl_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    crawl_run_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
) -> WebsiteCrawlRunResponse:
    return WebsiteCrawlRunResponse.model_validate(
        await read_crawl(db, workspace_id, product_id, current_user.id, crawl_run_id)
    )


@website_intelligence_router.get(
    "/{product_id}/website/pages", response_model=list[WebsitePageResponse]
)
async def list_website_pages_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[WebsitePageResponse]:
    return [
        WebsitePageResponse.model_validate(row)
        for row in await read_pages(
            db, workspace_id, product_id, current_user.id, limit=limit, offset=offset
        )
    ]


@website_intelligence_router.get(
    "/{product_id}/website/relevance", response_model=WebsitePageRelevanceResponse
)
async def get_website_page_relevance_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    topic: Annotated[str, Query(min_length=1, max_length=500)],
    current_user: CurrentUserDep,
    db: DbDep,
) -> WebsitePageRelevanceResponse:
    return await read_topic_relevance(db, workspace_id, product_id, current_user.id, topic)


@website_intelligence_router.get(
    "/{product_id}/website/health", response_model=WebsiteHealthResponse
)
async def get_website_health_endpoint(
    workspace_id: UUID, product_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> WebsiteHealthResponse:
    return await read_health(db, workspace_id, product_id, current_user.id)


@website_intelligence_router.get(
    "/{product_id}/website/capability-mappings",
    response_model=list[WebsiteCapabilityMappingResponse],
)
async def list_website_capability_mappings_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
    crawl_run_id: Annotated[UUID | None, Query(alias="crawlRunId")] = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[WebsiteCapabilityMappingResponse]:
    return [
        WebsiteCapabilityMappingResponse.model_validate(row)
        for row in await read_capability_mappings(
            db,
            workspace_id,
            product_id,
            current_user.id,
            crawl_run_id=crawl_run_id,
            limit=limit,
            offset=offset,
        )
    ]
