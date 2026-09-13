from typing import Annotated, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse

from app.api.deps import BrowserSessionDep, DbDep, MutationUserDep, get_current_user
from app.bootstrap.search_intelligence import SearchIntelligenceResources
from app.core.config import get_settings
from app.modules.identity.models.users import User
from app.modules.integrations.providers.credential_vault_provider import CredentialVaultError
from app.modules.search_intelligence.providers.search.search_provider import SearchProviderError
from app.modules.search_intelligence.schemas.search_connection_schema import (
    SearchAuthorizationResponse,
    SearchConnectRequest,
    SearchPropertyRefreshRequest,
    SearchPropertyResponse,
    SearchPropertySelectionRequest,
    SearchSourceResponse,
)
from app.modules.search_intelligence.services.search_connection_service import (
    GOOGLE_PROVIDER,
    cached_properties,
    compatibility,
    create_authorization,
    provider_key,
    read_source,
    refresh_properties,
    require_product_access,
    save_connection_and_properties,
    select_property,
)
from app.shared.exceptions import BadRequestError

product_search_router = APIRouter(
    prefix="/workspaces/{workspace_id}/products/{product_id}/search", tags=["search-intelligence"]
)
search_callback_router = APIRouter(prefix="/integrations/search", tags=["search-intelligence"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


def _resources(request: Request) -> SearchIntelligenceResources:
    return cast(SearchIntelligenceResources, request.app.state.search_intelligence)


def _provider(resources: SearchIntelligenceResources, public_provider: str):  # type: ignore[no-untyped-def]
    key = provider_key(public_provider)
    try:
        return resources.providers.get(key)
    except SearchProviderError as exc:
        raise BadRequestError(
            "Search provider is not configured.", code="search_provider_not_configured"
        ) from exc


def _transactions(resources: SearchIntelligenceResources):  # type: ignore[no-untyped-def]
    if resources.oauth_transactions is None:
        raise BadRequestError("Search OAuth is not configured.", code="search_oauth_not_configured")
    return resources.oauth_transactions


def _vault(resources: SearchIntelligenceResources):  # type: ignore[no-untyped-def]
    if resources.credential_vault is None:
        raise BadRequestError(
            "Search credential encryption is not configured.",
            code="search_credentials_not_configured",
        )
    return resources.credential_vault


def _property_response(item, website) -> SearchPropertyResponse:  # type: ignore[no-untyped-def]
    return SearchPropertyResponse(
        id=item.id,
        provider="google" if item.provider == "google_search" else item.provider,
        provider_property_id=item.provider_property_id,
        display_name=item.display_name,
        property_type=item.property_type,
        permission_level=item.permission_level,
        compatibility_status=compatibility(item, website),
        last_seen_at=item.last_seen_at,
    )


def _source_response(source, item, initial_sync_run=None) -> SearchSourceResponse:  # type: ignore[no-untyped-def]
    return SearchSourceResponse(
        id=source.id,
        provider="google" if source.provider == "google_search" else source.provider,
        property_id=source.property_id,
        provider_property_id=item.provider_property_id,
        website_source_id=source.website_source_id,
        status=source.status,
        selected_at=source.selected_at,
        disconnected_at=source.disconnected_at,
        latest_successful_data_date=source.latest_successful_data_date,
        initial_sync_run=initial_sync_run,
    )


def _frontend_redirect(return_path: str, status: str) -> str:
    parsed = urlsplit(return_path)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["search"] = status
    path = urlunsplit(("", "", parsed.path, urlencode(query), ""))
    return f"{get_settings().frontend_url}{path}"


@product_search_router.post("/connect", response_model=SearchAuthorizationResponse)
async def connect_search(
    workspace_id: UUID,
    product_id: UUID,
    body: SearchConnectRequest,
    request: Request,
    current_user: MutationUserDep,
    db: DbDep,
) -> SearchAuthorizationResponse:
    resources = _resources(request)
    provider = _provider(resources, body.provider)
    settings = get_settings()
    redirect_uri = (
        f"{settings.public_backend_url}{settings.api_v1_prefix}"
        f"/integrations/search/{body.provider}/callback"
    )
    authorization_url = await create_authorization(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=current_user.id,
        return_path=body.return_path,
        redirect_uri=redirect_uri,
        provider=provider,
        transactions=_transactions(resources),
    )
    return SearchAuthorizationResponse(provider=body.provider, authorization_url=authorization_url)


@search_callback_router.get(
    "/{provider}/callback",
    response_class=RedirectResponse,
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    responses={
        307: {
            "description": (
                "Backend-owned redirect to the validated product-bound returnPath with "
                "search=connected, search=denied, or search=error."
            )
        }
    },
)
async def search_callback(
    provider: str,
    request: Request,
    principal: BrowserSessionDep,
    db: DbDep,
    state: str,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    user, _ = principal
    resources = _resources(request)
    adapter = _provider(resources, provider)
    consumed = await _transactions(resources).consume_for_actor(
        db, provider=adapter.provider_key, actor_id=user.id, state=state
    )
    binding = consumed.binding
    if (
        binding.purpose != "search_authorization"
        or binding.subject_type != "product"
        or binding.subject_id is None
    ):
        raise BadRequestError("Invalid OAuth transaction.", code="invalid_oauth_state")
    await require_product_access(db, binding.workspace_id, binding.subject_id, user.id, write=True)
    if error is not None:
        return RedirectResponse(_frontend_redirect(consumed.return_path, "denied"))
    if not code:
        return RedirectResponse(_frontend_redirect(consumed.return_path, "error"))
    try:
        grant = await adapter.exchange_code(
            code=code,
            code_verifier=consumed.code_verifier,
            redirect_uri=consumed.redirect_uri,
        )
        await save_connection_and_properties(
            db,
            workspace_id=binding.workspace_id,
            product_id=binding.subject_id,
            actor_id=user.id,
            provider=adapter,
            grant=grant,
            vault=_vault(resources),
        )
    except (CredentialVaultError, SearchProviderError):
        return RedirectResponse(_frontend_redirect(consumed.return_path, "error"))
    return RedirectResponse(_frontend_redirect(consumed.return_path, "connected"))


@product_search_router.get("/properties", response_model=list[SearchPropertyResponse])
async def list_properties(
    workspace_id: UUID, product_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> list[SearchPropertyResponse]:
    items, website = await cached_properties(db, workspace_id, product_id, current_user.id)
    return [_property_response(item, website) for item in items]


@product_search_router.post("/properties/refresh", response_model=list[SearchPropertyResponse])
async def refresh_search_properties(
    workspace_id: UUID,
    product_id: UUID,
    body: SearchPropertyRefreshRequest,
    request: Request,
    current_user: MutationUserDep,
    db: DbDep,
) -> list[SearchPropertyResponse]:
    resources = _resources(request)
    items, website = await refresh_properties(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=current_user.id,
        provider=_provider(resources, body.provider),
        vault=_vault(resources),
    )
    return [_property_response(item, website) for item in items]


@product_search_router.post("/property-selection", response_model=SearchSourceResponse)
async def select_search_property(
    workspace_id: UUID,
    product_id: UUID,
    body: SearchPropertySelectionRequest,
    request: Request,
    current_user: MutationUserDep,
    db: DbDep,
) -> SearchSourceResponse:
    resources = _resources(request)
    provider = resources.providers.get(GOOGLE_PROVIDER)
    source, item, initial = await select_property(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=current_user.id,
        property_id=body.property_id,
        capabilities=provider.capabilities,
    )
    return _source_response(source, item, initial)


@product_search_router.get("/source", response_model=SearchSourceResponse)
async def get_search_source(
    workspace_id: UUID, product_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> SearchSourceResponse:
    source, item = await read_source(db, workspace_id, product_id, current_user.id)
    return _source_response(source, item)


__all__ = ["product_search_router", "search_callback_router"]
