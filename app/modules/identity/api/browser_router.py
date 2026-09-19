from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import BrowserSessionDep, CookieAuthCsrfDep
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token_claims,
    pkce_s256_challenge,
    sign_csrf_token,
    verify_csrf_token,
)
from app.db.session import get_db
from app.modules.identity.models.oauth import AuthSession
from app.modules.identity.schemas.oauth import (
    BrowserSessionResponse,
    BrowserWorkspaceUpdate,
    CurrentWorkspaceResponse,
    EmailVerificationRequest,
    OAuthProviderResponse,
    PasswordLoginRequest,
    PasswordRegistrationRequest,
    PrimaryIdentityResponse,
)
from app.modules.identity.schemas.users import UserResponse
from app.modules.identity.services.browser_oauth import (
    consume_oauth_transaction,
    create_oauth_transaction,
    delete_user_account,
    get_bound_session,
    list_available_workspaces,
    provision_oauth_session,
    switch_session_workspace,
)
from app.modules.identity.services.password_auth import (
    login_with_password,
    register_password_user,
    verify_registration_code,
)
from app.modules.identity.services.sessions import (
    get_refresh_session,
    refresh_session_is_active,
    revoke_auth_session,
    revoke_refresh_session,
    rotate_refresh_session,
)
from app.services.oauth import OAuthProvider, OAuthProviderError, OAuthProviderRegistry
from app.shared.exceptions import BadRequestError, UnauthorizedError
from app.shared.responses import MessageResponse

router = APIRouter(prefix="/auth/browser", tags=["browser-auth"])
DbDep = Annotated[AsyncSession, Depends(get_db)]


def registry(request: Request) -> OAuthProviderRegistry:
    return cast(OAuthProviderRegistry, request.app.state.oauth_providers)


def _provider(request: Request, name: str) -> OAuthProvider:
    provider = registry(request).get(name)
    if provider is None:
        raise BadRequestError("OAuth provider is not configured.", code="provider_not_configured")
    return provider


def _set_session_cookies(
    response: Response, *, user_id: UUID, session_id: UUID, refresh: str
) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.browser_access_cookie_name,
        create_access_token(str(user_id), session_id=str(session_id)),
        httponly=True,
        path="/",
        max_age=settings.access_token_expire_minutes * 60,
        secure=settings.browser_cookie_secure,
        samesite=settings.browser_cookie_samesite,
        domain=settings.browser_cookie_domain,
    )
    response.set_cookie(
        settings.browser_refresh_cookie_name,
        refresh,
        httponly=True,
        path=settings.browser_refresh_cookie_path,
        max_age=settings.refresh_token_expire_days * 86400,
        secure=settings.browser_cookie_secure,
        samesite=settings.browser_cookie_samesite,
        domain=settings.browser_cookie_domain,
    )
    response.set_cookie(
        settings.browser_csrf_cookie_name,
        sign_csrf_token(str(session_id)),
        httponly=False,
        path="/",
        max_age=settings.refresh_token_expire_days * 86400,
        secure=settings.browser_cookie_secure,
        samesite=settings.browser_cookie_samesite,
        domain=settings.browser_cookie_domain,
    )


def _clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    cookies = [
        (settings.browser_access_cookie_name, "/"),
        (settings.browser_csrf_cookie_name, "/"),
        (settings.browser_refresh_cookie_name, settings.browser_refresh_cookie_path),
        (settings.browser_refresh_cookie_name, f"{settings.api_v1_prefix}/auth/browser"),
    ]
    for name, path in dict.fromkeys(cookies):
        response.delete_cookie(name, path=path, domain=settings.browser_cookie_domain)


def _primary_identity(identity: object | None) -> PrimaryIdentityResponse | None:
    return (
        PrimaryIdentityResponse.model_validate(identity, from_attributes=True)
        if identity is not None
        else None
    )


def _browser_claims(request: Request) -> tuple[UUID, UUID]:
    token = request.cookies.get(get_settings().browser_access_cookie_name)
    claims = decode_access_token_claims(token) if token else None
    if claims is None:
        raise UnauthorizedError("Browser session is missing or expired.", code="missing_session")
    try:
        return UUID(claims["sub"]), UUID(claims["sid"])
    except ValueError as exc:
        raise UnauthorizedError("Invalid browser session.", code="invalid_session") from exc


def _require_origin_and_csrf(request: Request, session_id: UUID) -> None:
    settings = get_settings()
    if request.headers.get("origin") not in settings.resolved_browser_allowed_origins:
        raise BadRequestError("Origin is not allowed.", code="invalid_origin")
    cookie = request.cookies.get(settings.browser_csrf_cookie_name)
    header = request.headers.get(settings.browser_csrf_header_name)
    if not cookie or cookie != header or not verify_csrf_token(cookie, str(session_id)):
        raise BadRequestError("Invalid CSRF token.", code="invalid_csrf_token")


@router.get("/providers", response_model=list[OAuthProviderResponse])
def providers(request: Request) -> list[OAuthProviderResponse]:
    return [
        OAuthProviderResponse(name=item.name, display_name=item.display_name)
        for item in registry(request).list()
    ]


@router.post("/register", status_code=202, response_model=MessageResponse)
async def register_password_account(
    payload: PasswordRegistrationRequest, db: DbDep
) -> MessageResponse:
    await register_password_user(
        db, name=payload.name, email=str(payload.email), password=payload.password
    )
    return MessageResponse(message="Verification code sent.")


@router.post("/verify-email", response_model=MessageResponse)
async def verify_password_account(payload: EmailVerificationRequest, db: DbDep) -> MessageResponse:
    await verify_registration_code(db, email=str(payload.email), code=payload.code)
    return MessageResponse(message="Email verified successfully.")


@router.post("/login", response_model=BrowserSessionResponse)
async def login_password_account(
    payload: PasswordLoginRequest, request: Request, response: Response, db: DbDep
) -> BrowserSessionResponse:
    provisioned = await login_with_password(
        db,
        email=str(payload.email),
        password=payload.password,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_session_cookies(
        response,
        user_id=provisioned.user.id,
        session_id=provisioned.session.id,
        refresh=provisioned.refresh_token,
    )
    user, workspace, role, linked, identity = await get_bound_session(
        db, user_id=provisioned.user.id, session_id=provisioned.session.id
    )
    return BrowserSessionResponse(
        user=UserResponse.model_validate(user),
        current_workspace=CurrentWorkspaceResponse(id=workspace.id, name=workspace.name, role=role),
        linked_providers=linked,
        primary_identity=_primary_identity(identity),
    )


@router.get("/oauth/{provider_name}/authorize")
async def authorize(
    provider_name: str,
    request: Request,
    db: DbDep,
    return_path: str = Query("/dashboard", alias="returnPath"),
) -> RedirectResponse:
    provider = _provider(request, provider_name)
    settings = get_settings()
    redirect_uri = (
        f"{settings.public_backend_url}{settings.api_v1_prefix}"
        f"/auth/browser/oauth/{provider.name}/callback"
    )
    transaction = await create_oauth_transaction(
        db,
        provider=provider.name,
        return_path=return_path,
        redirect_uri=redirect_uri,
    )
    return RedirectResponse(
        provider.authorization_url(
            state=transaction.state,
            code_challenge=pkce_s256_challenge(transaction.code_verifier),
            redirect_uri=redirect_uri,
        )
    )


@router.get("/oauth/{provider_name}/callback", name="callback")
async def callback(
    provider_name: str, request: Request, code: str, state: str, db: DbDep
) -> RedirectResponse:
    provider = _provider(request, provider_name)
    verifier, return_path, redirect_uri = await consume_oauth_transaction(
        db, provider=provider.name, state=state
    )
    try:
        identity = await provider.exchange_code(
            code=code, code_verifier=verifier, redirect_uri=redirect_uri
        )
    except OAuthProviderError as exc:
        raise BadRequestError(str(exc), code="oauth_provider_error") from exc
    provisioned = await provision_oauth_session(
        db,
        provider=provider.name,
        identity_data=identity,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    response = RedirectResponse(f"{get_settings().frontend_url}{return_path}")
    _set_session_cookies(
        response,
        user_id=provisioned.user.id,
        session_id=provisioned.session.id,
        refresh=provisioned.refresh_token,
    )
    return response


@router.get("/session", response_model=BrowserSessionResponse)
async def session(request: Request, db: DbDep) -> BrowserSessionResponse:
    user_id, session_id = _browser_claims(request)
    user, workspace, role, linked, identity = await get_bound_session(
        db, user_id=user_id, session_id=session_id
    )
    return BrowserSessionResponse(
        user=UserResponse.model_validate(user),
        current_workspace=CurrentWorkspaceResponse(id=workspace.id, name=workspace.name, role=role),
        linked_providers=linked,
        primary_identity=_primary_identity(identity),
    )


@router.get("/workspaces", response_model=list[CurrentWorkspaceResponse])
async def available_workspaces(
    principal: BrowserSessionDep, db: DbDep
) -> list[CurrentWorkspaceResponse]:
    user, _ = principal
    workspaces = await list_available_workspaces(db, user.id)
    return [
        CurrentWorkspaceResponse(id=workspace.id, name=workspace.name, role=role)
        for workspace, role in workspaces
    ]


@router.patch("/session/workspace", response_model=BrowserSessionResponse)
async def update_session_workspace(
    payload: BrowserWorkspaceUpdate, principal: CookieAuthCsrfDep, db: DbDep
) -> BrowserSessionResponse:
    user, session_id = principal
    bound_user, workspace, role, linked, identity = await switch_session_workspace(
        db,
        user_id=user.id,
        session_id=session_id,
        workspace_id=payload.workspace_id,
    )
    return BrowserSessionResponse(
        user=UserResponse.model_validate(bound_user),
        current_workspace=CurrentWorkspaceResponse(id=workspace.id, name=workspace.name, role=role),
        linked_providers=linked,
        primary_identity=_primary_identity(identity),
    )


@router.post("/refresh", response_model=BrowserSessionResponse)
async def refresh(request: Request, response: Response, db: DbDep) -> BrowserSessionResponse:
    token = request.cookies.get(get_settings().browser_refresh_cookie_name)
    if not token:
        raise UnauthorizedError("Refresh token is missing.", code="missing_refresh_token")
    existing = await get_refresh_session(db, token)
    if not refresh_session_is_active(existing):
        raise UnauthorizedError("Invalid refresh token.", code="invalid_refresh_token")
    assert existing is not None
    _require_origin_and_csrf(request, existing.id)
    user, replacement, new_session = await rotate_refresh_session(
        db,
        token,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    bound_user, workspace, role, linked, identity = await get_bound_session(
        db, user_id=user.id, session_id=new_session.id
    )
    _set_session_cookies(response, user_id=user.id, session_id=new_session.id, refresh=replacement)
    return BrowserSessionResponse(
        user=UserResponse.model_validate(bound_user),
        current_workspace=CurrentWorkspaceResponse(id=workspace.id, name=workspace.name, role=role),
        linked_providers=linked,
        primary_identity=_primary_identity(identity),
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request, response: Response, db: DbDep) -> MessageResponse:
    settings = get_settings()
    token = request.cookies.get(settings.browser_refresh_cookie_name)
    existing = await get_refresh_session(db, token) if token else None
    active_session = existing if refresh_session_is_active(existing) else None
    if active_session is None:
        try:
            user_id, session_id = _browser_claims(request)
        except UnauthorizedError:
            pass
        else:
            candidate = await db.get(AuthSession, session_id)
            if (
                candidate is not None
                and candidate.user_id == user_id
                and refresh_session_is_active(candidate)
            ):
                active_session = candidate
    if active_session is not None:
        _require_origin_and_csrf(request, active_session.id)
        if existing is active_session and token is not None:
            await revoke_refresh_session(db, token)
        else:
            await revoke_auth_session(db, active_session)
    _clear_session_cookies(response)
    return MessageResponse(message="Logged out successfully.")


@router.delete("/account", response_model=MessageResponse)
async def delete_account(
    principal: CookieAuthCsrfDep, response: Response, db: DbDep
) -> MessageResponse:
    user, _ = principal
    await delete_user_account(db, user)
    _clear_session_cookies(response)
    return MessageResponse(message="Account deleted successfully.")
