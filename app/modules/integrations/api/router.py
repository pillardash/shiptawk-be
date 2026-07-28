from typing import Annotated, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import (
    BrowserSessionDep,
    CookieAuthCsrfDep,
    DbDep,
    MutationUserDep,
    get_current_user,
)
from app.core.config import get_settings
from app.core.security import pkce_s256_challenge
from app.modules.identity.models.users import User
from app.modules.integrations.models import IntegrationConnection
from app.modules.integrations.policies.access import require_repository_write_role, workspace_role
from app.modules.integrations.providers.github import GitHubAppError, GitHubAppProvider
from app.modules.integrations.providers.x_oauth import (
    IntegrationCredentialCipher,
    XOAuthProvider,
    XOAuthProviderError,
)
from app.modules.integrations.repositories import (
    get_latest_x_connection,
    list_workspace_connections,
)
from app.modules.integrations.schemas import (
    ChangelogDigestSettingsUpdateRequest,
    InstallationAttachRequest,
    InstallationResponse,
    InstallationStatusResponse,
    IntegrationConnectionResponse,
    RepositoryDetailUpdateRequest,
    RepositoryResponse,
    TrackingUpdateRequest,
    XConnectionStatusResponse,
)
from app.modules.integrations.services.connections import (
    attach_github_installation,
    disconnect_x_integration,
    sync_github_repositories,
    update_repository_fields,
)
from app.modules.integrations.services.x_oauth import (
    consume_x_oauth_transaction,
    create_x_oauth_transaction,
    save_x_connection,
)
from app.modules.repos.models import repos
from app.modules.workspaces.models import WorkspaceRole
from app.shared.exceptions import BadRequestError, ForbiddenError, NotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["workspace-repositories"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


def _provider(request: Request) -> GitHubAppProvider:
    provider = getattr(request.app.state, "github_app", None)
    if provider is None:
        raise BadRequestError("GitHub App is not configured.", code="github_app_not_configured")
    return cast(GitHubAppProvider, provider)


def _x_oauth_provider(request: Request) -> XOAuthProvider:
    provider = getattr(request.app.state, "x_oauth_provider", None)
    if provider is None:
        raise BadRequestError("X OAuth is not configured.", code="x_oauth_not_configured")
    return cast(XOAuthProvider, provider)


def _credential_cipher(request: Request) -> IntegrationCredentialCipher:
    cipher = getattr(request.app.state, "integration_credential_cipher", None)
    if cipher is None:
        raise BadRequestError(
            "Integration credential encryption is not configured.",
            code="integration_credentials_not_configured",
        )
    return cast(IntegrationCredentialCipher, cipher)


async def _membership(db: DbDep, workspace_id: UUID, user_id: UUID) -> WorkspaceRole:
    return await workspace_role(db, workspace_id, user_id)


async def _require_repository_write_role(db: DbDep, workspace_id: UUID, user_id: UUID) -> None:
    await require_repository_write_role(db, workspace_id, user_id)


async def _x_connection(db: DbDep, workspace_id: UUID) -> IntegrationConnection | None:
    return await get_latest_x_connection(db, workspace_id)


def _x_status(connection: IntegrationConnection | None) -> XConnectionStatusResponse:
    if connection is None:
        return XConnectionStatusResponse(
            connected=False,
            status=None,
            account_id=None,
            username=None,
            scopes=[],
            token_expires_at=None,
        )
    return XConnectionStatusResponse(
        connected=connection.status == "active",
        status=connection.status,
        account_id=connection.external_account_id,
        username=connection.external_username,
        scopes=connection.scopes,
        token_expires_at=connection.token_expires_at,
    )


def _safe_connection(connection: IntegrationConnection) -> IntegrationConnectionResponse:
    return IntegrationConnectionResponse(
        id=connection.id,
        provider="x" if connection.provider == "twitter" else connection.provider,
        account_id=connection.external_account_id,
        username=connection.external_username,
        status=connection.status,
        scopes=connection.scopes,
        token_expires_at=connection.token_expires_at,
        connected_at=connection.created_at,
    )


def _frontend_redirect(return_path: str, status: str) -> str:
    parsed = urlsplit(return_path)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["x"] = status
    path = urlunsplit(("", "", parsed.path, urlencode(query), ""))
    return f"{get_settings().frontend_url}{path}"


@router.get(
    "/connections", response_model=list[IntegrationConnectionResponse], tags=["integrations"]
)
async def list_connections(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> list[IntegrationConnectionResponse]:
    await _membership(db, workspace_id, current_user.id)
    connections = await list_workspace_connections(db, workspace_id)
    return [_safe_connection(connection) for connection in connections]


@router.get("/x/authorize", tags=["integrations"])
async def authorize_x_connection(
    workspace_id: UUID,
    request: Request,
    principal: BrowserSessionDep,
    db: DbDep,
    return_path: str = Query("/settings", alias="returnPath"),
) -> RedirectResponse:
    user, _ = principal
    role = await _membership(db, workspace_id, user.id)
    if role not in {WorkspaceRole.owner, WorkspaceRole.admin}:
        raise ForbiddenError("Workspace administration required.", code="workspace_access_denied")
    provider = _x_oauth_provider(request)
    settings = get_settings()
    redirect_uri = (
        f"{settings.public_backend_url}{settings.api_v1_prefix}"
        f"/workspaces/{workspace_id}/x/callback"
    )
    state, verifier = await create_x_oauth_transaction(
        db,
        workspace_id=workspace_id,
        user_id=user.id,
        return_path=return_path,
        redirect_uri=redirect_uri,
    )
    return RedirectResponse(
        provider.authorization_url(
            state=state,
            code_challenge=pkce_s256_challenge(verifier),
            redirect_uri=redirect_uri,
        )
    )


@router.get("/x/callback", tags=["integrations"])
async def x_connection_callback(
    workspace_id: UUID,
    request: Request,
    principal: BrowserSessionDep,
    db: DbDep,
    state: str,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    user, _ = principal
    role = await _membership(db, workspace_id, user.id)
    if role not in {WorkspaceRole.owner, WorkspaceRole.admin}:
        raise ForbiddenError("Workspace administration required.", code="workspace_access_denied")
    verifier, return_path, redirect_uri = await consume_x_oauth_transaction(
        db, workspace_id=workspace_id, user_id=user.id, state=state
    )
    if error is not None:
        return RedirectResponse(_frontend_redirect(return_path, "denied"))
    if not code:
        return RedirectResponse(_frontend_redirect(return_path, "error"))
    try:
        grant = await _x_oauth_provider(request).exchange_code(
            code=code, code_verifier=verifier, redirect_uri=redirect_uri
        )
        await save_x_connection(
            db,
            workspace_id=workspace_id,
            user_id=user.id,
            grant=grant,
            cipher=_credential_cipher(request),
        )
    except XOAuthProviderError:
        return RedirectResponse(_frontend_redirect(return_path, "error"))
    return RedirectResponse(_frontend_redirect(return_path, "connected"))


@router.get("/x/connection", response_model=XConnectionStatusResponse, tags=["integrations"])
async def x_connection_status(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> XConnectionStatusResponse:
    await _membership(db, workspace_id, current_user.id)
    return _x_status(await _x_connection(db, workspace_id))


@router.delete("/x/connection", response_model=XConnectionStatusResponse, tags=["integrations"])
async def disconnect_x_connection(
    workspace_id: UUID, current_user: MutationUserDep, db: DbDep
) -> XConnectionStatusResponse:
    role = await _membership(db, workspace_id, current_user.id)
    if role not in {WorkspaceRole.owner, WorkspaceRole.admin}:
        raise ForbiddenError("Workspace administration required.", code="workspace_access_denied")
    if await _x_connection(db, workspace_id) is None:
        return _x_status(None)
    await disconnect_x_integration(db, workspace_id, current_user.id)
    return _x_status(None)


def _repo(row: object) -> RepositoryResponse:
    mapping = row._mapping  # type: ignore[attr-defined]
    return RepositoryResponse.model_validate(mapping)


@router.post("/github/installation", response_model=InstallationResponse)
async def attach_installation(
    workspace_id: UUID,
    body: InstallationAttachRequest,
    request: Request,
    principal: CookieAuthCsrfDep,
    db: DbDep,
) -> InstallationResponse:
    user, _ = principal
    role = await _membership(db, workspace_id, user.id)
    if role not in {WorkspaceRole.owner, WorkspaceRole.admin}:
        raise ForbiddenError("Workspace administration required.", code="workspace_access_denied")
    try:
        installation = await _provider(request).verify_installation(body.installation_id)
    except GitHubAppError as exc:
        raise BadRequestError(str(exc), code="github_app_error") from exc
    await attach_github_installation(db, workspace_id, installation)
    return InstallationResponse(
        installation_id=installation.id,
        account_login=installation.account_login,
        status="active",
    )


async def _connection(db: DbDep, workspace_id: UUID) -> IntegrationConnection:
    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == "github_app",
            IntegrationConnection.status == "active",
            IntegrationConnection.deleted_at.is_(None),
        )
    )
    if connection is None:
        raise NotFoundError("GitHub App installation not attached.", code="installation_not_found")
    return connection


@router.get("/github/installation", response_model=InstallationStatusResponse)
async def installation_status(
    workspace_id: UUID, principal: BrowserSessionDep, db: DbDep
) -> InstallationStatusResponse:
    user, _ = principal
    await _require_repository_write_role(db, workspace_id, user.id)
    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == "github_app",
            IntegrationConnection.deleted_at.is_(None),
        )
    )
    if connection is None:
        return InstallationStatusResponse(
            connected=False,
            status=None,
            account_login=None,
            installation_id=None,
        )
    try:
        installation_id = int(connection.external_account_id)
    except ValueError:
        installation_id = None
    return InstallationStatusResponse(
        connected=connection.status == "active",
        status=connection.status,
        account_login=connection.external_username,
        installation_id=installation_id,
    )


@router.post("/repositories/sync", response_model=list[RepositoryResponse])
async def sync_repositories(
    workspace_id: UUID,
    request: Request,
    principal: CookieAuthCsrfDep,
    db: DbDep,
) -> list[RepositoryResponse]:
    user, _ = principal
    await _require_repository_write_role(db, workspace_id, user.id)
    connection = await _connection(db, workspace_id)
    try:
        remote = await _provider(request).list_repositories(int(connection.external_account_id))
    except GitHubAppError as exc:
        raise BadRequestError(str(exc), code="github_app_error") from exc
    await sync_github_repositories(db, workspace_id, user.id, remote)
    return await list_repositories(workspace_id, principal, db)


@router.get("/repositories", response_model=list[RepositoryResponse])
async def list_repositories(
    workspace_id: UUID, principal: BrowserSessionDep, db: DbDep
) -> list[RepositoryResponse]:
    user, _ = principal
    await _membership(db, workspace_id, user.id)
    rows = (await db.execute(select(repos).where(repos.c.workspace_id == workspace_id))).all()
    return [_repo(row) for row in rows]


@router.get("/repositories/{repo_id}", response_model=RepositoryResponse)
async def get_repository(
    workspace_id: UUID, repo_id: UUID, principal: BrowserSessionDep, db: DbDep
) -> RepositoryResponse:
    user, _ = principal
    await _membership(db, workspace_id, user.id)
    row = (
        await db.execute(
            select(repos).where(repos.c.id == repo_id, repos.c.workspace_id == workspace_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Repository not found.", code="repository_not_found")
    return _repo(row)


@router.patch("/repositories/{repo_id}", response_model=RepositoryResponse)
async def update_repository_detail(
    workspace_id: UUID,
    repo_id: UUID,
    body: RepositoryDetailUpdateRequest,
    principal: CookieAuthCsrfDep,
    db: DbDep,
) -> RepositoryResponse:
    user, _ = principal
    await _require_repository_write_role(db, workspace_id, user.id)
    row = await update_repository_fields(
        db, workspace_id, repo_id, tracked_branch=body.tracked_branch
    )
    return _repo(row)


@router.patch(
    "/repositories/{repo_id}/changelog-digest-settings", response_model=RepositoryResponse
)
async def update_changelog_digest_settings(
    workspace_id: UUID,
    repo_id: UUID,
    body: ChangelogDigestSettingsUpdateRequest,
    principal: CookieAuthCsrfDep,
    db: DbDep,
) -> RepositoryResponse:
    user, _ = principal
    await _require_repository_write_role(db, workspace_id, user.id)
    row = await update_repository_fields(
        db,
        workspace_id,
        repo_id,
        changelog_digest_enabled=body.enabled,
        changelog_digest_frequency=body.frequency,
    )
    return _repo(row)


@router.patch("/repositories/{repo_id}/tracking", response_model=RepositoryResponse)
async def update_tracking(
    workspace_id: UUID,
    repo_id: UUID,
    body: TrackingUpdateRequest,
    principal: CookieAuthCsrfDep,
    db: DbDep,
) -> RepositoryResponse:
    user, _ = principal
    await _require_repository_write_role(db, workspace_id, user.id)
    row = await update_repository_fields(db, workspace_id, repo_id, is_tracked=body.is_tracked)
    return _repo(row)
