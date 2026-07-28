from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status

from app.api.deps import DbDep, MutationUserDep, get_current_user
from app.bootstrap.search_intelligence import SearchIntelligenceResources
from app.modules.identity.models.users import User
from app.modules.search_intelligence.schemas.search_sync_schema import (
    SearchSyncRequest,
    SearchSyncRunResponse,
)
from app.modules.search_intelligence.services.search_connection_service import (
    GOOGLE_PROVIDER,
    require_product_access,
)
from app.modules.search_intelligence.services.search_sync_service import (
    read_search_sync,
    request_search_sync,
)
from app.shared.exceptions import BadRequestError

search_sync_router = APIRouter(
    prefix="/workspaces/{workspace_id}/products/{product_id}/search/syncs",
    tags=["search-intelligence"],
)
CurrentUserDep = Annotated[User, Depends(get_current_user)]


def _capabilities(request: Request):  # type: ignore[no-untyped-def]
    resources = cast(SearchIntelligenceResources, request.app.state.search_intelligence)
    try:
        return resources.providers.get(GOOGLE_PROVIDER).capabilities
    except Exception as exc:
        raise BadRequestError(
            "Search provider is not configured.", code="search_provider_not_configured"
        ) from exc


@search_sync_router.post(
    "", response_model=SearchSyncRunResponse, status_code=status.HTTP_202_ACCEPTED
)
async def create_search_sync(
    workspace_id: UUID,
    product_id: UUID,
    request: Request,
    current_user: MutationUserDep,
    db: DbDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    body: SearchSyncRequest | None = None,
) -> SearchSyncRunResponse:
    body = body or SearchSyncRequest()
    await require_product_access(db, workspace_id, product_id, current_user.id, write=True)
    run = await request_search_sync(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=current_user.id,
        idempotency_key=idempotency_key,
        trigger=body.trigger,
        capabilities=_capabilities(request),
    )
    return SearchSyncRunResponse.model_validate(run)


@search_sync_router.get("/{sync_run_id}", response_model=SearchSyncRunResponse)
async def get_search_sync(
    workspace_id: UUID,
    product_id: UUID,
    sync_run_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
) -> SearchSyncRunResponse:
    await require_product_access(db, workspace_id, product_id, current_user.id, write=False)
    return SearchSyncRunResponse.model_validate(
        await read_search_sync(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            run_id=sync_run_id,
        )
    )


__all__ = ["search_sync_router"]
