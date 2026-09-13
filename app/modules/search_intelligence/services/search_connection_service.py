import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import pkce_s256_challenge
from app.modules.analytics.services.product_event_service import write_product_event
from app.modules.integrations.models import IntegrationConnection
from app.modules.integrations.providers.credential_vault_provider import IntegrationCredentialVault
from app.modules.integrations.repositories.integration_connection_repository import (
    get_current_provider_connection,
    lock_retained_provider_connection,
    revoke_local_credentials,
)
from app.modules.integrations.services.oauth_transaction_service import (
    IntegrationOAuthTransactionBinding,
    IntegrationOAuthTransactionService,
)
from app.modules.products.models import WebsiteSource
from app.modules.products.repositories.product_repository import get_product_for_workspace
from app.modules.search_intelligence.enums.search_intelligence_enum import SearchPropertyType
from app.modules.search_intelligence.models import SearchProviderProperty, SearchSource
from app.modules.search_intelligence.policies.search_property_policy import (
    classify_property_compatibility,
)
from app.modules.search_intelligence.providers.search.search_provider import (
    SearchAuthorizationGrant,
    SearchProvider,
    SearchProviderAuthorizationError,
    SearchProviderCapabilities,
    SearchProviderError,
)
from app.modules.search_intelligence.repositories.search_connection_repository import (
    cancel_active_runs,
    disconnect_connection_sources,
    disconnect_source,
    get_active_search_source,
    get_active_website_source,
    get_connection_property,
    list_connection_properties,
    lock_workspace,
)
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.services.access import resolve_active_workspace_role
from app.shared.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError

GOOGLE_PROVIDER = "google_search"
REQUIRED_GOOGLE_SCOPES = frozenset(
    {"openid", "email", "https://www.googleapis.com/auth/webmasters.readonly"}
)
REFRESH_SKEW = timedelta(minutes=5)
ADMIN_ROLES = frozenset({WorkspaceRole.owner, WorkspaceRole.admin})


def provider_key(public_provider: str) -> str:
    if public_provider == "google":
        return GOOGLE_PROVIDER
    raise BadRequestError("Search provider is unsupported.", code="search_provider_unsupported")


async def require_product_access(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    *,
    write: bool,
) -> None:
    if await get_product_for_workspace(db, workspace_id, product_id, actor_id) is None:
        raise NotFoundError("Search intelligence not found.", code="search_intelligence_not_found")
    role = await resolve_active_workspace_role(db, workspace_id, actor_id)
    if write and role not in ADMIN_ROLES:
        raise ForbiddenError(
            "Workspace administration required.", code="search_intelligence_forbidden"
        )


async def require_website(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, *, for_update: bool = False
) -> WebsiteSource:
    source = await get_active_website_source(db, workspace_id, product_id, for_update=for_update)
    if source is None:
        raise ConflictError("An active website source is required.", code="website_source_inactive")
    return source


async def create_authorization(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    return_path: str,
    redirect_uri: str,
    provider: SearchProvider,
    transactions: IntegrationOAuthTransactionService,
) -> str:
    await require_product_access(db, workspace_id, product_id, actor_id, write=True)
    await require_website(db, workspace_id, product_id)
    created = await transactions.create(
        db,
        binding=IntegrationOAuthTransactionBinding(
            workspace_id=workspace_id,
            actor_id=actor_id,
            provider=provider.provider_key,
            purpose="search_authorization",
            subject_type="product",
            subject_id=product_id,
        ),
        return_path=return_path,
        redirect_uri=redirect_uri,
    )
    return provider.build_authorization_url(
        redirect_uri=redirect_uri,
        state=created.state,
        code_challenge=pkce_s256_challenge(created.code_verifier),
    )


def _grant_credentials(
    grant: SearchAuthorizationGrant, refresh_token: str | None
) -> dict[str, str]:
    values = {
        "accessToken": grant.access_token,
        "expiresAt": grant.expires_at.isoformat() if grant.expires_at else "",
        "scopes": json.dumps(list(grant.scopes), separators=(",", ":")),
        "tokenType": grant.token_type,
    }
    if refresh_token:
        values["refreshToken"] = refresh_token
    return values


def decode_grant(credentials: dict[str, str]) -> SearchAuthorizationGrant:
    try:
        scopes = json.loads(credentials["scopes"])
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
            raise ValueError
        expires_raw = credentials.get("expiresAt", "")
        return SearchAuthorizationGrant(
            access_token=credentials["accessToken"],
            refresh_token=credentials.get("refreshToken"),
            expires_at=datetime.fromisoformat(expires_raw) if expires_raw else None,
            scopes=tuple(scopes),
            token_type=credentials["tokenType"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BadRequestError(
            "Search credentials are unavailable.", code="search_credentials_unavailable"
        ) from exc


async def save_connection_and_properties(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    provider: SearchProvider,
    grant: SearchAuthorizationGrant,
    vault: IntegrationCredentialVault,
) -> IntegrationConnection:
    if provider.provider_key == GOOGLE_PROVIDER and not REQUIRED_GOOGLE_SCOPES.issubset(
        grant.scopes
    ):
        raise SearchProviderError("required_scopes_missing")
    identity = await provider.get_identity(grant)
    properties = await provider.list_properties(grant)
    await lock_workspace(db, workspace_id)
    connection = await lock_retained_provider_connection(db, workspace_id, provider.provider_key)
    preserved_refresh = None
    if (
        grant.refresh_token is None
        and connection is not None
        and connection.credentials_ciphertext
        and connection.credential_key_version
    ):
        preserved_refresh = vault.decrypt(
            connection.credentials_ciphertext, connection.credential_key_version
        ).get("refreshToken")
    encrypted = vault.encrypt(_grant_credentials(grant, grant.refresh_token or preserved_refresh))
    now = datetime.now(UTC)
    if connection is None:
        connection = IntegrationConnection(
            workspace_id=workspace_id,
            provider=provider.provider_key,
            external_account_id=identity.subject,
            idempotency_key=f"{provider.provider_key}:{workspace_id}",
            created_by=actor_id,
        )
        db.add(connection)
        await db.flush()
    elif connection.external_account_id != identity.subject:
        source_ids = await disconnect_connection_sources(
            db, workspace_id, connection.id, actor_id, now
        )
        await cancel_active_runs(db, workspace_id, source_ids, now)
        for retired_property in await list_connection_properties(
            db, workspace_id, connection.id, include_retired=True
        ):
            retired_property.deleted_at = now
            retired_property.updated_by = actor_id
    connection.external_account_id = identity.subject
    connection.external_username = identity.email
    connection.credentials_ciphertext = encrypted.ciphertext
    connection.credential_key_version = encrypted.key_version
    connection.scopes = list(grant.scopes)
    connection.status = "active"
    connection.token_expires_at = grant.expires_at
    connection.last_verified_at = now
    connection.revoked_at = None
    connection.deleted_at = None
    connection.updated_by = actor_id
    website = await require_website(db, workspace_id, product_id)
    cached = {
        item.provider_property_id: item
        for item in await list_connection_properties(
            db, workspace_id, connection.id, include_retired=True
        )
    }
    returned_ids = {remote.provider_property_id for remote in properties}
    for provider_property_id, cached_item in cached.items():
        if provider_property_id not in returned_ids:
            cached_item.deleted_at = now
            cached_item.updated_by = actor_id
    for remote in properties:
        item = cached.get(remote.provider_property_id)
        compatibility = classify_property_compatibility(
            property_type=SearchPropertyType(remote.property_type),
            provider_property_id=remote.provider_property_id,
            normalized_website_url=website.normalized_base_url,
        )
        if item is None:
            item = SearchProviderProperty(
                workspace_id=workspace_id,
                integration_connection_id=connection.id,
                provider=provider.provider_key,
                provider_property_id=remote.provider_property_id,
                first_discovered_at=now,
                created_by=actor_id,
            )
            db.add(item)
        item.display_name = remote.display_name
        item.property_type = remote.property_type
        item.permission_level = remote.permission_level
        item.compatibility_status = compatibility.value
        item.last_seen_at = now
        item.deleted_at = None
        item.updated_by = actor_id
    await write_product_event(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=actor_id,
        event_name="search_console_connected",
        idempotency_key=f"search-console-connected:{connection.id}:{product_id}",
        resource_type="integration_connection",
        resource_id=connection.id,
    )
    await db.commit()
    await db.refresh(connection)
    return connection


async def current_connection(db: AsyncSession, workspace_id: UUID) -> IntegrationConnection:
    connection = await get_current_provider_connection(db, workspace_id, GOOGLE_PROVIDER)
    if connection is None:
        raise ConflictError("Search provider is not connected.", code="search_not_connected")
    return connection


async def cached_properties(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, actor_id: UUID
) -> tuple[list[SearchProviderProperty], WebsiteSource]:
    await require_product_access(db, workspace_id, product_id, actor_id, write=False)
    website = await require_website(db, workspace_id, product_id)
    connection = await current_connection(db, workspace_id)
    return await list_connection_properties(db, workspace_id, connection.id), website


async def refresh_properties(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    provider: SearchProvider,
    vault: IntegrationCredentialVault,
) -> tuple[list[SearchProviderProperty], WebsiteSource]:
    await require_product_access(db, workspace_id, product_id, actor_id, write=True)
    await lock_workspace(db, workspace_id)
    connection = await lock_retained_provider_connection(db, workspace_id, provider.provider_key)
    if connection is None or connection.status != "active" or connection.revoked_at is not None:
        raise ConflictError("Search provider is not connected.", code="search_not_connected")
    if not connection.credentials_ciphertext or not connection.credential_key_version:
        raise ConflictError("Search provider is not connected.", code="search_not_connected")
    grant = decode_grant(
        dict(vault.decrypt(connection.credentials_ciphertext, connection.credential_key_version))
    )
    now = datetime.now(UTC)
    expires_at = grant.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is not None and expires_at <= now + REFRESH_SKEW:
        try:
            refreshed = await provider.refresh_authorization(grant)
        except SearchProviderAuthorizationError:
            connection.status = "expired"
            await db.commit()
            raise
        refresh_token = refreshed.refresh_token or grant.refresh_token
        encrypted = vault.encrypt(_grant_credentials(refreshed, refresh_token))
        connection.credentials_ciphertext = encrypted.ciphertext
        connection.credential_key_version = encrypted.key_version
        connection.scopes = list(refreshed.scopes)
        connection.token_expires_at = refreshed.expires_at
        connection.updated_by = actor_id
        grant = SearchAuthorizationGrant(
            refreshed.access_token,
            refresh_token,
            refreshed.expires_at,
            refreshed.scopes,
            refreshed.token_type,
        )
    await save_connection_and_properties(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=actor_id,
        provider=provider,
        grant=grant,
        vault=vault,
    )
    return await cached_properties(db, workspace_id, product_id, actor_id)


def compatibility(item: SearchProviderProperty, website: WebsiteSource) -> str:
    return classify_property_compatibility(
        property_type=SearchPropertyType(item.property_type),
        provider_property_id=item.provider_property_id,
        normalized_website_url=website.normalized_base_url,
    ).value


async def select_property(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    property_id: UUID,
    capabilities: SearchProviderCapabilities,
) -> tuple[SearchSource, SearchProviderProperty, object]:
    from app.modules.search_intelligence.services.search_sync_service import create_initial_sync

    await require_product_access(db, workspace_id, product_id, actor_id, write=True)
    website = await require_website(db, workspace_id, product_id, for_update=True)
    connection = await current_connection(db, workspace_id)
    item = await get_connection_property(db, workspace_id, connection.id, property_id)
    if item is None:
        raise NotFoundError("Search property not found.", code="search_property_not_found")
    if item.permission_level == "siteUnverifiedUser":
        raise ConflictError(
            "Search property does not permit data access.",
            code="search_property_permission_insufficient",
        )
    if compatibility(item, website) != "compatible":
        raise ConflictError("Search property is incompatible.", code="search_property_incompatible")
    active = await get_active_search_source(db, workspace_id, product_id, for_update=True)
    if active is not None and active.property_id == item.id:
        initial = await create_initial_sync(db, source=active, capabilities=capabilities)
        await db.commit()
        return active, item, initial
    now = datetime.now(UTC)
    if active is not None:
        await disconnect_source(active, actor_id, now)
        await db.flush()
    source = SearchSource(
        workspace_id=workspace_id,
        product_id=product_id,
        integration_connection_id=connection.id,
        property_id=item.id,
        website_source_id=website.id,
        provider=connection.provider,
        status="active",
        selected_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(source)
    await db.flush()
    initial = await create_initial_sync(db, source=source, capabilities=capabilities)
    await write_product_event(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=actor_id,
        event_name="search_property_selected",
        idempotency_key=f"search-property-selected:{source.id}",
        resource_type="search_source",
        resource_id=source.id,
    )
    await db.commit()
    await db.refresh(source)
    await db.refresh(initial)
    return source, item, initial


async def read_source(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, actor_id: UUID
) -> tuple[SearchSource, SearchProviderProperty]:
    await require_product_access(db, workspace_id, product_id, actor_id, write=False)
    source = await get_active_search_source(db, workspace_id, product_id)
    if source is None:
        raise NotFoundError("Search source not found.", code="search_source_not_found")
    item = await get_connection_property(
        db, workspace_id, source.integration_connection_id, source.property_id
    )
    if item is None:
        raise NotFoundError("Search source not found.", code="search_source_not_found")
    return source, item


async def disconnect_product_source(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, actor_id: UUID
) -> None:
    await require_product_access(db, workspace_id, product_id, actor_id, write=True)
    source = await get_active_search_source(db, workspace_id, product_id, for_update=True)
    if source is None:
        await db.commit()
        return
    now = datetime.now(UTC)
    await disconnect_source(source, actor_id, now)
    await cancel_active_runs(db, workspace_id, [source.id], now)
    await db.commit()


async def revoke_workspace_provider(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    actor_id: UUID,
    public_provider: str,
    provider: SearchProvider,
    vault: IntegrationCredentialVault,
) -> None:
    role = await resolve_active_workspace_role(db, workspace_id, actor_id)
    if role is None:
        raise NotFoundError("Search connection not found.", code="search_connection_not_found")
    if role not in ADMIN_ROLES:
        raise ForbiddenError(
            "Workspace administration required.", code="search_intelligence_forbidden"
        )
    key = provider_key(public_provider)
    if provider.provider_key != key:
        raise BadRequestError("Search provider is unsupported.", code="search_provider_unsupported")
    connection = await lock_retained_provider_connection(db, workspace_id, key)
    if connection is None:
        await db.commit()
        return
    if connection.credentials_ciphertext and connection.credential_key_version:
        try:
            grant = decode_grant(
                dict(
                    vault.decrypt(
                        connection.credentials_ciphertext, connection.credential_key_version
                    )
                )
            )
            await provider.revoke(grant)
        except Exception:  # Remote cleanup is best effort; local revocation is authoritative.
            pass
    now = datetime.now(UTC)
    source_ids = await disconnect_connection_sources(db, workspace_id, connection.id, actor_id, now)
    await cancel_active_runs(db, workspace_id, source_ids, now)
    await revoke_local_credentials(
        db,
        workspace_id=workspace_id,
        connection_id=connection.id,
        actor_id=actor_id,
        revoked_at=now,
    )
    await db.commit()


__all__ = [
    "GOOGLE_PROVIDER",
    "cached_properties",
    "compatibility",
    "create_authorization",
    "decode_grant",
    "disconnect_product_source",
    "provider_key",
    "read_source",
    "refresh_properties",
    "require_product_access",
    "revoke_workspace_provider",
    "save_connection_and_properties",
    "select_property",
]
