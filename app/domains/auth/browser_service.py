from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import generate_pkce_verifier, generate_token, hash_token
from app.domains.auth.models import AuthSession, OAuthIdentity, OAuthTransaction
from app.domains.auth.service import create_refresh_session_record
from app.domains.users.models import User
from app.domains.workspaces.models import Workspace, WorkspaceMembership, WorkspaceRole
from app.services.oauth import OAuthIdentityData
from app.shared.exceptions import BadRequestError, ForbiddenError, UnauthorizedError


@dataclass(slots=True)
class OAuthStart:
    state: str
    code_verifier: str


@dataclass(slots=True)
class ProvisionedSession:
    user: User
    workspace: Workspace
    role: WorkspaceRole
    refresh_token: str
    session: AuthSession


def _fernet() -> Fernet:
    key = get_settings().oauth_transaction_encryption_key
    if key is None:
        raise RuntimeError("OAuth transaction encryption is not configured.")
    return Fernet(key.get_secret_value().encode("ascii"))


async def create_oauth_transaction(
    db: AsyncSession, *, provider: str, return_path: str, redirect_uri: str
) -> OAuthStart:
    if not return_path.startswith("/") or return_path.startswith("//"):
        raise BadRequestError("Invalid return path.", code="invalid_return_path")
    state = generate_token()
    verifier = generate_pkce_verifier()
    db.add(
        OAuthTransaction(
            provider=provider,
            state_hash=hash_token(state),
            code_verifier_ciphertext=_fernet().encrypt(verifier.encode()).decode(),
            return_path=return_path,
            redirect_uri=redirect_uri,
            expires_at=datetime.now(UTC)
            + timedelta(minutes=get_settings().oauth_transaction_expire_minutes),
        )
    )
    await db.commit()
    return OAuthStart(state=state, code_verifier=verifier)


async def consume_oauth_transaction(
    db: AsyncSession, *, provider: str, state: str
) -> tuple[str, str, str]:
    now = datetime.now(UTC)
    row = (
        await db.execute(
            update(OAuthTransaction)
            .where(
                OAuthTransaction.provider == provider,
                OAuthTransaction.state_hash == hash_token(state),
                OAuthTransaction.consumed_at.is_(None),
                OAuthTransaction.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(
                OAuthTransaction.code_verifier_ciphertext,
                OAuthTransaction.return_path,
                OAuthTransaction.redirect_uri,
            )
        )
    ).one_or_none()
    if row is None:
        await db.rollback()
        raise BadRequestError("Invalid or expired OAuth state.", code="invalid_oauth_state")
    await db.commit()
    try:
        verifier = _fernet().decrypt(row.code_verifier_ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise BadRequestError("Invalid OAuth transaction.", code="invalid_oauth_state") from exc
    return verifier, row.return_path, row.redirect_uri


async def provision_oauth_session(
    db: AsyncSession,
    *,
    provider: str,
    identity_data: OAuthIdentityData,
    user_agent: str | None,
    ip_address: str | None,
) -> ProvisionedSession:
    now = datetime.now(UTC)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        lock_key = f"oauth-identity:{provider}:{identity_data.subject}"
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )
    identity = (
        await db.scalars(
            select(OAuthIdentity)
            .where(
                OAuthIdentity.provider == provider,
                OAuthIdentity.provider_subject == identity_data.subject,
            )
            .with_for_update()
        )
    ).first()
    if identity is None:
        user = User(
            email=identity_data.email,
            is_verified=identity_data.email_verified,
            github_id=identity_data.subject if provider == "github" else None,
            github_username=identity_data.username if provider == "github" else None,
        )
        db.add(user)
        await db.flush()
        display = identity_data.display_name or identity_data.username or "Personal"
        workspace = Workspace(name=f"{display}'s workspace")
        db.add(workspace)
        await db.flush()
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.owner,
        )
        identity = OAuthIdentity(
            user_id=user.id,
            provider=provider,
            provider_subject=identity_data.subject,
            email=identity_data.email,
            email_verified=identity_data.email_verified,
            username=identity_data.username,
            display_name=identity_data.display_name,
            avatar_url=identity_data.avatar_url,
            last_login_at=now,
        )
        db.add_all([membership, identity])
        role = WorkspaceRole.owner
    else:
        if identity.deleted_at is not None:
            raise UnauthorizedError(
                "Deleted OAuth identities are retained and cannot be restored automatically.",
                code="deleted_identity",
            )
        existing_user = await db.get(User, identity.user_id)
        if existing_user is None or existing_user.deleted_at is not None:
            raise UnauthorizedError("OAuth identity is unavailable.", code="invalid_identity")
        if not existing_user.is_active:
            raise UnauthorizedError("User account is inactive.", code="inactive_user")
        existing_membership = (
            await db.scalars(
                select(WorkspaceMembership)
                .where(
                    WorkspaceMembership.user_id == existing_user.id,
                    WorkspaceMembership.is_active.is_(True),
                )
                .order_by(WorkspaceMembership.created_at)
            )
        ).first()
        if existing_membership is None:
            raise ForbiddenError("No active workspace membership.", code="workspace_access_denied")
        existing_workspace = await db.get(Workspace, existing_membership.workspace_id)
        if existing_workspace is None or existing_workspace.deleted_at is not None:
            raise ForbiddenError("No active workspace membership.", code="workspace_access_denied")
        user = existing_user
        membership = existing_membership
        workspace = existing_workspace
        role = existing_membership.role
        identity.email = identity_data.email
        identity.email_verified = identity_data.email_verified
        identity.username = identity_data.username
        identity.display_name = identity_data.display_name
        identity.avatar_url = identity_data.avatar_url
        identity.last_login_at = now
        if provider == "github":
            user.github_id = identity_data.subject
            user.github_username = identity_data.username

    refresh_token, session = await create_refresh_session_record(
        db,
        user,
        current_workspace_id=workspace.id,
        user_agent=user_agent,
        ip_address=ip_address,
        commit=False,
    )
    await db.commit()
    return ProvisionedSession(user, workspace, role, refresh_token, session)


async def get_bound_session(
    db: AsyncSession, *, user_id: UUID, session_id: UUID
) -> tuple[User, Workspace, WorkspaceRole, list[str]]:
    auth_row = (
        await db.execute(
            select(User, AuthSession)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(
                User.id == user_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                AuthSession.id == session_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.deleted_at.is_(None),
                AuthSession.expires_at > datetime.now(UTC),
            )
        )
    ).one_or_none()
    if auth_row is None:
        raise UnauthorizedError("Invalid or revoked browser session.", code="invalid_session")
    row = (
        await db.execute(
            select(Workspace, WorkspaceMembership.role)
            .join(
                WorkspaceMembership,
                (WorkspaceMembership.workspace_id == Workspace.id)
                & (WorkspaceMembership.user_id == user_id),
            )
            .where(
                Workspace.id == auth_row.AuthSession.current_workspace_id,
                Workspace.deleted_at.is_(None),
                WorkspaceMembership.is_active.is_(True),
            )
        )
    ).one_or_none()
    if row is None:
        raise ForbiddenError("Workspace access denied.", code="workspace_access_denied")
    providers = list(
        await db.scalars(
            select(OAuthIdentity.provider)
            .where(OAuthIdentity.user_id == user_id, OAuthIdentity.deleted_at.is_(None))
            .distinct()
            .order_by(OAuthIdentity.provider)
        )
    )
    return auth_row.User, row.Workspace, row.role, providers
