from datetime import date
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import DbDep, MutationUserDep, get_current_user
from app.bootstrap.search_intelligence import SearchIntelligenceResources
from app.modules.identity.models.users import User
from app.modules.search_intelligence.api.search_connection_router import (
    _provider,
    _resources,
    _vault,
)
from app.modules.search_intelligence.schemas.search_read_schema import (
    SearchBaselineResponse,
    SearchHealthResponse,
    SearchPagePageResponse,
    SearchQueryPageResponse,
    SearchReadinessResponse,
)
from app.modules.search_intelligence.services.search_connection_service import (
    disconnect_product_source,
    revoke_workspace_provider,
)
from app.modules.search_intelligence.services.search_read_service import (
    DEFAULT_LIMIT,
    read_baseline,
    read_health,
    read_pages,
    read_queries,
    read_readiness,
)

search_read_router = APIRouter(
    prefix="/workspaces/{workspace_id}/products/{product_id}/search",
    tags=["search-intelligence"],
)
workspace_search_router = APIRouter(
    prefix="/workspaces/{workspace_id}/search", tags=["search-intelligence"]
)
CurrentUserDep = Annotated[User, Depends(get_current_user)]


def _capabilities(request: Request):  # type: ignore[no-untyped-def]
    return _provider(_resources(request), "google").capabilities


@search_read_router.get("/baseline", response_model=SearchBaselineResponse)
async def get_search_baseline(
    workspace_id: UUID,
    product_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    db: DbDep,
    cutoff_date: Annotated[date | None, Query(alias="cutoffDate")] = None,
) -> SearchBaselineResponse:
    return await read_baseline(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=current_user.id,
        capabilities=_capabilities(request),
        cutoff_date=cutoff_date,
    )


@search_read_router.get("/queries", response_model=SearchQueryPageResponse)
async def get_search_queries(
    workspace_id: UUID,
    product_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    db: DbDep,
    start_date: Annotated[date | None, Query(alias="startDate")] = None,
    end_date: Annotated[date | None, Query(alias="endDate")] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> SearchQueryPageResponse:
    return await read_queries(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=current_user.id,
        capabilities=_capabilities(request),
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@search_read_router.get("/pages", response_model=SearchPagePageResponse)
async def get_search_pages(
    workspace_id: UUID,
    product_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    db: DbDep,
    start_date: Annotated[date | None, Query(alias="startDate")] = None,
    end_date: Annotated[date | None, Query(alias="endDate")] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> SearchPagePageResponse:
    return await read_pages(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=current_user.id,
        capabilities=_capabilities(request),
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@search_read_router.get("/health", response_model=SearchHealthResponse)
async def get_search_health(
    workspace_id: UUID,
    product_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    db: DbDep,
) -> SearchHealthResponse:
    return await read_health(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=current_user.id,
        capabilities=_capabilities(request),
    )


@search_read_router.get("/readiness", response_model=SearchReadinessResponse)
async def get_search_readiness(
    workspace_id: UUID,
    product_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    db: DbDep,
) -> SearchReadinessResponse:
    return await read_readiness(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=current_user.id,
        capabilities=_capabilities(request),
    )


@search_read_router.delete("/source", status_code=status.HTTP_204_NO_CONTENT)
async def delete_search_source(
    workspace_id: UUID,
    product_id: UUID,
    current_user: MutationUserDep,
    db: DbDep,
) -> Response:
    await disconnect_product_source(db, workspace_id, product_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@workspace_search_router.delete("/connections/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_search_connection(
    workspace_id: UUID,
    provider: str,
    request: Request,
    current_user: MutationUserDep,
    db: DbDep,
) -> Response:
    resources = cast(SearchIntelligenceResources, request.app.state.search_intelligence)
    await revoke_workspace_provider(
        db,
        workspace_id=workspace_id,
        actor_id=current_user.id,
        public_provider=provider,
        provider=_provider(resources, provider),
        vault=_vault(resources),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["search_read_router", "workspace_search_router"]
