from collections.abc import AsyncGenerator, Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bootstrap.search_intelligence import SearchIntelligenceResources
from app.core.security import create_access_token, hash_token
from app.db.base import Base
from app.db.outbox import OutboxEvent
from app.db.session import get_db
from app.main import create_app
from app.modules.analytics.models import ProductEvent
from app.modules.identity.models.oauth import AuthSession
from app.modules.identity.models.users import User
from app.modules.integrations.models import IntegrationConnection
from app.modules.integrations.providers.credential_vault_provider import (
    DeterministicFakeIntegrationCredentialVault,
)
from app.modules.products.enums.website_intelligence_enum import WebsiteSourceStatus
from app.modules.products.models import Product, WebsiteSource
from app.modules.search_intelligence.models import (
    SearchDailyMetric,
    SearchPage,
    SearchQuery,
    SearchSource,
    SearchSyncRun,
)
from app.modules.search_intelligence.models import (
    SearchProviderProperty as CachedProperty,
)
from app.modules.search_intelligence.providers.search.fake_search_provider import (
    FakeSearchProvider,
)
from app.modules.search_intelligence.providers.search.search_provider import (
    SearchAuthorizationGrant,
    SearchProviderAuthorizationError,
    SearchProviderError,
    SearchProviderIdentity,
    SearchProviderProperty,
)
from app.modules.search_intelligence.providers.search.search_registry_provider import (
    SearchProviderRegistry,
)
from app.modules.search_intelligence.services.search_sync_service import SearchSyncService
from app.modules.workspaces.models import Workspace, WorkspaceMembership, WorkspaceRole

REQUIRED_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/webmasters.readonly",
)


def browser_event_url(workspace: Workspace, product: Product) -> str:
    return f"/v1/workspaces/{workspace.id}/products/{product.id}/analytics/events"


class GoogleSearchFake(FakeSearchProvider):
    provider_key = "google_search"

    def __init__(self) -> None:
        super().__init__(
            properties=(
                SearchProviderProperty(
                    "sc-domain:example.com", "example.com", "domain", "siteOwner"
                ),
                SearchProviderProperty(
                    "https://other.example/", "Other", "url_prefix", "siteFullUser"
                ),
                SearchProviderProperty(
                    "https://www.example.com/", "Website", "url_prefix", "siteOwner"
                ),
            )
        )
        self.refresh_token: str | None = "refresh-secret"
        self.scopes: tuple[str, ...] = REQUIRED_SCOPES
        self.fail_revoke = False
        self.refresh_error = False

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> SearchAuthorizationGrant:
        self.calls.append(("exchange_code", (code, code_verifier, redirect_uri)))
        return SearchAuthorizationGrant(
            "access-secret",
            self.refresh_token,
            datetime.now(UTC) + timedelta(hours=1),
            self.scopes,
            "Bearer",
        )

    async def revoke(self, grant: SearchAuthorizationGrant) -> None:
        self.calls.append(("revoke", grant))
        if self.fail_revoke:
            raise SearchProviderError("remote_revoke_failed")

    async def refresh_authorization(
        self, grant: SearchAuthorizationGrant
    ) -> SearchAuthorizationGrant:
        self.calls.append(("refresh_authorization", grant))
        if self.refresh_error:
            raise SearchProviderAuthorizationError("invalid_grant")
        return SearchAuthorizationGrant(
            "rotated-access", None, datetime.now(UTC) + timedelta(hours=1), grant.scopes, "Bearer"
        )


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def browser(user: User, session: AuthSession) -> dict[str, str]:
    return {"access_token": create_access_token(str(user.id), session_id=str(session.id))}


async def seed(
    sessions: async_sessionmaker[AsyncSession],
    *,
    role: WorkspaceRole = WorkspaceRole.owner,
    website: bool = True,
) -> tuple[User, Workspace, Product, AuthSession]:
    async with sessions() as db:
        user = User(email=f"search-{uuid4()}@example.com")
        workspace = Workspace(name="Search workspace")
        db.add_all([user, workspace])
        await db.flush()
        product = Product(workspace_id=workspace.id, user_id=user.id, name="Product")
        db.add(product)
        await db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=user.id, role=role, is_active=True
            )
        )
        if website:
            db.add(
                WebsiteSource(
                    workspace_id=workspace.id,
                    product_id=product.id,
                    base_url="https://www.example.com/",
                    normalized_base_url="https://www.example.com/",
                    status=WebsiteSourceStatus.active,
                    created_by=user.id,
                )
            )
        session = AuthSession(
            user_id=user.id,
            refresh_token_hash=hash_token(f"refresh-{uuid4()}"),
            expires_at=datetime.now(UTC) + timedelta(days=1),
            family_id=uuid4(),
            generation=0,
            current_workspace_id=workspace.id,
        )
        db.add(session)
        await db.commit()
        return user, workspace, product, session


def connect(
    client: TestClient, user: User, workspace: Workspace, product: Product
) -> tuple[dict[str, object], str]:
    response = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/connect",
        json={"provider": "google", "returnPath": f"/users/products/{product.id}?tab=search"},
        headers=auth(user),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    state = parse_qs(urlsplit(payload["authorizationUrl"]).query)["state"][0]
    return payload, state


def connect_and_select(
    client: TestClient,
    user: User,
    workspace: Workspace,
    product: Product,
    session: AuthSession,
) -> dict[str, object]:
    _, state = connect(client, user, workspace, product)
    callback = client.get(
        "/v1/integrations/search/google/callback",
        params={"code": "code", "state": state},
        cookies=browser(user, session),
        follow_redirects=False,
    )
    assert callback.status_code == 307
    properties = client.get(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/properties",
        headers=auth(user),
    ).json()
    property_id = next(
        item["id"] for item in properties if item["providerPropertyId"] == "sc-domain:example.com"
    )
    selected = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/property-selection",
        json={"propertyId": property_id},
        headers=auth(user),
    )
    assert selected.status_code == 200, selected.text
    return cast(dict[str, object], selected.json())


@pytest.fixture
def search_client() -> Generator[
    tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
    None,
    None,
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    provider = GoogleSearchFake()
    registry = SearchProviderRegistry([provider])
    vault = DeterministicFakeIntegrationCredentialVault()
    app = create_app()
    existing = app.state.resources.search_intelligence
    app.state.search_intelligence = SearchIntelligenceResources(
        registry, None, existing.oauth_transactions, vault
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, sessions, provider, vault
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


def test_connect_enforces_admin_product_tenancy_and_active_website(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, provider, _ = search_client
    assert client.portal is not None
    owner, workspace, product, _ = client.portal.call(seed, sessions)

    async def seed_viewer() -> tuple[User, Workspace, Product, AuthSession]:
        return await seed(sessions, role=WorkspaceRole.viewer)

    async def seed_without_site() -> tuple[User, Workspace, Product, AuthSession]:
        return await seed(sessions, website=False)

    viewer, viewer_workspace, viewer_product, _ = client.portal.call(seed_viewer)
    no_site_user, no_site_workspace, no_site_product, _ = client.portal.call(seed_without_site)

    async def add_owner_to_viewer_workspace() -> None:
        async with sessions() as db:
            db.add(
                WorkspaceMembership(
                    workspace_id=viewer_workspace.id,
                    user_id=owner.id,
                    role=WorkspaceRole.admin,
                    is_active=True,
                )
            )
            await db.commit()

    client.portal.call(add_owner_to_viewer_workspace)

    payload, _ = connect(client, owner, workspace, product)
    forbidden = client.post(
        f"/v1/workspaces/{viewer_workspace.id}/products/{viewer_product.id}/search/connect",
        json={"provider": "google"},
        headers=auth(viewer),
    )
    hidden = client.post(
        f"/v1/workspaces/{viewer_workspace.id}/products/{product.id}/search/connect",
        json={"provider": "google"},
        headers=auth(owner),
    )
    inactive = client.post(
        f"/v1/workspaces/{no_site_workspace.id}/products/{no_site_product.id}/search/connect",
        json={"provider": "google"},
        headers=auth(no_site_user),
    )
    unsupported = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/connect",
        json={"provider": "bing"},
        headers=auth(owner),
    )

    assert payload["provider"] == "google"
    assert payload["status"] == "authorization_pending"
    authorize_call = next(call for call in provider.calls if call[0] == "build_authorization_url")
    authorize_args = cast(tuple[str, str, str], authorize_call[1])
    assert authorize_args[0] == "http://localhost:8000/v1/integrations/search/google/callback"
    assert forbidden.status_code == 403
    assert hidden.status_code == 404
    assert inactive.status_code == 409
    assert unsupported.status_code == 400
    assert unsupported.json()["code"] == "search_provider_unsupported"


def test_callback_encrypts_reuses_connection_preserves_refresh_and_caches_properties(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, provider, vault = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    _, state = connect(client, user, workspace, product)
    callback = client.get(
        "/v1/integrations/search/google/callback",
        params={"code": "code", "state": state},
        cookies=browser(user, session),
        follow_redirects=False,
    )
    listed = client.get(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/properties",
        headers=auth(user),
    )

    assert callback.status_code == 307
    assert callback.headers["location"].endswith(
        f"/users/products/{product.id}?tab=search&search=connected"
    )
    assert listed.status_code == 200
    statuses = {item["providerPropertyId"]: item["compatibilityStatus"] for item in listed.json()}
    assert statuses == {
        "sc-domain:example.com": "compatible",
        "https://other.example/": "incompatible",
        "https://www.example.com/": "compatible",
    }
    assert "secret" not in str(listed.json())
    callback_contract = client.get("/openapi.json").json()["paths"][
        "/v1/integrations/search/{provider}/callback"
    ]["get"]
    assert "307" in callback_contract["responses"]

    async def persisted() -> tuple[UUID, dict[str, str], int]:
        async with sessions() as db:
            connection = (await db.scalars(select(IntegrationConnection))).one()
            assert connection.credentials_ciphertext is not None
            assert connection.credential_key_version is not None
            values = dict(
                vault.decrypt(connection.credentials_ciphertext, connection.credential_key_version)
            )
            count = int(await db.scalar(select(func.count()).select_from(CachedProperty)) or 0)
            return connection.id, values, count

    connection_id, credentials, property_count = client.portal.call(persisted)
    assert credentials["accessToken"] == "access-secret"
    assert credentials["refreshToken"] == "refresh-secret"
    assert all(isinstance(value, str) for value in credentials.values())
    assert property_count == 3

    provider.refresh_token = None
    _, second_state = connect(client, user, workspace, product)
    second = client.get(
        "/v1/integrations/search/google/callback",
        params={"code": "second", "state": second_state},
        cookies=browser(user, session),
        follow_redirects=False,
    )
    replay = client.get(
        "/v1/integrations/search/google/callback",
        params={"code": "second", "state": second_state},
        cookies=browser(user, session),
        follow_redirects=False,
    )
    assert second.status_code == 307
    assert replay.status_code == 400
    next_id, next_credentials, _ = client.portal.call(persisted)
    assert next_id == connection_id
    assert next_credentials["refreshToken"] == "refresh-secret"


def test_workspace_revoke_then_same_account_reconnect_reactivates_one_encrypted_row(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, _, _ = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    connect_and_select(client, user, workspace, product, session)
    assert (
        client.delete(
            f"/v1/workspaces/{workspace.id}/search/connections/google",
            headers=auth(user),
        ).status_code
        == 204
    )

    _, state = connect(client, user, workspace, product)
    response = client.get(
        "/v1/integrations/search/google/callback",
        params={"code": "reconnect", "state": state},
        cookies=browser(user, session),
        follow_redirects=False,
    )

    async def inspect() -> tuple[int, str, bool, bool]:
        async with sessions() as db:
            rows = list(await db.scalars(select(IntegrationConnection)))
            return (
                len(rows),
                rows[0].status,
                rows[0].credentials_ciphertext is not None,
                rows[0].revoked_at is None,
            )

    assert response.status_code == 307
    assert client.portal.call(inspect) == (1, "active", True, True)


def test_different_account_reconnect_disconnects_all_sources_runs_and_old_properties(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, provider, _ = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    selected = connect_and_select(client, user, workspace, product, session)
    first_source_id = UUID(cast(str, selected["id"]))

    async def add_second_product() -> UUID:
        async with sessions() as db:
            first = await db.get(SearchSource, first_source_id)
            assert first is not None
            second_product = Product(workspace_id=workspace.id, user_id=user.id, name="Second")
            db.add(second_product)
            await db.flush()
            website = WebsiteSource(
                workspace_id=workspace.id,
                product_id=second_product.id,
                base_url="https://second.example.com/",
                normalized_base_url="https://second.example.com/",
                status="active",
                created_by=user.id,
            )
            db.add(website)
            await db.flush()
            source = SearchSource(
                workspace_id=workspace.id,
                product_id=second_product.id,
                integration_connection_id=first.integration_connection_id,
                property_id=first.property_id,
                website_source_id=website.id,
                provider=first.provider,
                status="active",
                selected_at=datetime.now(UTC),
                created_by=user.id,
            )
            db.add(source)
            await db.flush()
            db.add(
                SearchSyncRun(
                    workspace_id=workspace.id,
                    product_id=second_product.id,
                    source_id=source.id,
                    trigger="manual",
                    status="running",
                    idempotency_key=f"switch:{uuid4()}",
                    request_fingerprint="c" * 64,
                    requested_start_date=date(2026, 7, 1),
                    requested_end_date=date(2026, 7, 1),
                    policy_version="test",
                    schema_version=1,
                    lease_owner="old-worker",
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )
            await db.commit()
            return second_product.id

    client.portal.call(add_second_product)
    provider._identity = SearchProviderIdentity("different-account", "other@example.com")
    provider._properties = (
        SearchProviderProperty("sc-domain:new.example", "new.example", "domain", "siteOwner"),
    )
    _, state = connect(client, user, workspace, product)
    callback = client.get(
        "/v1/integrations/search/google/callback",
        params={"code": "different", "state": state},
        cookies=browser(user, session),
        follow_redirects=False,
    )

    async def inspect() -> tuple[list[str], list[str], int, int]:
        async with sessions() as db:
            sources = list(await db.scalars(select(SearchSource)))
            runs = list(await db.scalars(select(SearchSyncRun)))
            properties = list(await db.scalars(select(CachedProperty)))
            connections = list(await db.scalars(select(IntegrationConnection)))
            return (
                [source.status for source in sources],
                [run.status for run in runs],
                sum(item.deleted_at is not None for item in properties),
                len(connections),
            )

    source_statuses, run_statuses, retired_count, connection_count = client.portal.call(inspect)
    assert callback.status_code == 307
    assert source_statuses == ["disconnected", "disconnected"]
    assert set(run_statuses) == {"cancelled"}
    assert retired_count == 3
    assert connection_count == 1


def test_property_refresh_selection_rejects_other_connection_and_replaces_idempotently(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, _, _ = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    _, state = connect(client, user, workspace, product)
    client.get(
        "/v1/integrations/search/google/callback",
        params={"code": "code", "state": state},
        cookies=browser(user, session),
        follow_redirects=False,
    )
    properties = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/properties/refresh",
        json={"provider": "google"},
        headers=auth(user),
    ).json()
    by_provider_id = {item["providerPropertyId"]: item for item in properties}
    compatible_id = by_provider_id["sc-domain:example.com"]["id"]
    incompatible_id = by_provider_id["https://other.example/"]["id"]
    replacement_id = by_provider_id["https://www.example.com/"]["id"]

    async def add_other_connection_property() -> UUID:
        async with sessions() as db:
            other_connection = IntegrationConnection(
                workspace_id=workspace.id,
                provider="other_search",
                external_account_id="other",
                idempotency_key=f"other:{uuid4()}",
            )
            db.add(other_connection)
            await db.flush()
            item = CachedProperty(
                workspace_id=workspace.id,
                integration_connection_id=other_connection.id,
                provider="other_search",
                provider_property_id="sc-domain:example.com",
                display_name="Foreign",
                property_type="domain",
                permission_level="owner",
                compatibility_status="compatible",
                first_discovered_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
            db.add(item)
            await db.commit()
            return item.id

    foreign_id = client.portal.call(add_other_connection_property)

    incompatible = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/property-selection",
        json={"propertyId": incompatible_id},
        headers=auth(user),
    )
    foreign = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/property-selection",
        json={"propertyId": str(foreign_id)},
        headers=auth(user),
    )
    first = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/property-selection",
        json={"propertyId": compatible_id},
        headers=auth(user),
    )
    second = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/property-selection",
        json={"propertyId": compatible_id},
        headers=auth(user),
    )
    replacement = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/property-selection",
        json={"propertyId": replacement_id},
        headers=auth(user),
    )
    source = client.get(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/source",
        headers=auth(user),
    )

    assert incompatible.status_code == 409
    assert foreign.status_code == 404
    assert first.status_code == second.status_code == replacement.status_code == 200
    assert source.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["initialSyncRun"]["id"] == second.json()["initialSyncRun"]["id"]
    assert replacement.json()["id"] == source.json()["id"]
    assert replacement.json()["id"] != first.json()["id"]

    async def source_count() -> int:
        async with sessions() as db:
            return int(await db.scalar(select(func.count()).select_from(SearchSource)) or 0)

    assert client.portal.call(source_count) == 2


def test_property_refresh_retires_reactivates_validates_permissions_and_rotates_grant(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, provider, vault = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    _, state = connect(client, user, workspace, product)
    client.get(
        "/v1/integrations/search/google/callback",
        params={"code": "code", "state": state},
        cookies=browser(user, session),
        follow_redirects=False,
    )
    base = f"/v1/workspaces/{workspace.id}/products/{product.id}/search"
    original = client.get(f"{base}/properties", headers=auth(user)).json()
    retired_id = next(
        item["id"] for item in original if item["providerPropertyId"] == "sc-domain:example.com"
    )
    provider._properties = tuple(
        item
        for item in provider._properties
        if item.provider_property_id != "sc-domain:example.com"
    )
    assert (
        client.post(
            f"{base}/properties/refresh", json={"provider": "google"}, headers=auth(user)
        ).status_code
        == 200
    )
    retired_selection = client.post(
        f"{base}/property-selection",
        json={"propertyId": retired_id},
        headers=auth(user),
    )

    provider._properties = (
        SearchProviderProperty(
            "sc-domain:example.com", "example.com", "domain", "siteUnverifiedUser"
        ),
    )
    returned = client.post(
        f"{base}/properties/refresh", json={"provider": "google"}, headers=auth(user)
    )
    unverified = client.post(
        f"{base}/property-selection",
        json={"propertyId": returned.json()[0]["id"]},
        headers=auth(user),
    )

    async def expire_grant() -> None:
        async with sessions() as db:
            connection = (await db.scalars(select(IntegrationConnection))).one()
            expires_at = datetime(2026, 7, 1, tzinfo=UTC)
            grant = SearchAuthorizationGrant(
                "expired-access",
                "preserved-refresh",
                expires_at,
                REQUIRED_SCOPES,
                "Bearer",
            )
            encrypted = vault.encrypt(
                {
                    "accessToken": grant.access_token,
                    "refreshToken": cast(str, grant.refresh_token),
                    "expiresAt": expires_at.isoformat(),
                    "scopes": '["openid","email","https://www.googleapis.com/auth/webmasters.readonly"]',
                    "tokenType": "Bearer",
                }
            )
            connection.credentials_ciphertext = encrypted.ciphertext
            connection.credential_key_version = encrypted.key_version
            connection.token_expires_at = grant.expires_at
            await db.commit()

    client.portal.call(expire_grant)
    calls_before = len(provider.calls)
    refreshed = client.post(
        f"{base}/properties/refresh", json={"provider": "google"}, headers=auth(user)
    )

    async def credentials_and_status() -> tuple[dict[str, str], str]:
        async with sessions() as db:
            connection = (await db.scalars(select(IntegrationConnection))).one()
            assert connection.credentials_ciphertext and connection.credential_key_version
            return (
                dict(
                    vault.decrypt(
                        connection.credentials_ciphertext, connection.credential_key_version
                    )
                ),
                connection.status,
            )

    credentials, status = client.portal.call(credentials_and_status)
    assert retired_selection.status_code == 404
    assert returned.json()[0]["id"] == retired_id
    assert unverified.status_code == 409
    assert refreshed.status_code == 200
    assert [call[0] for call in provider.calls[calls_before:]][:2] == [
        "refresh_authorization",
        "get_identity",
    ]
    assert credentials["accessToken"] == "rotated-access"
    assert credentials["refreshToken"] == "preserved-refresh"
    assert status == "active"

    client.portal.call(expire_grant)
    provider.refresh_error = True
    list_calls = sum(call[0] == "list_properties" for call in provider.calls)
    with pytest.raises(SearchProviderAuthorizationError, match="invalid_grant"):
        client.post(f"{base}/properties/refresh", json={"provider": "google"}, headers=auth(user))
    _, status = client.portal.call(credentials_and_status)
    assert status == "expired"
    assert sum(call[0] == "list_properties" for call in provider.calls) == list_calls


def test_selection_enqueues_safe_initial_sync_and_manual_sync_is_idempotent(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, _, _ = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    _, state = connect(client, user, workspace, product)
    client.get(
        "/v1/integrations/search/google/callback",
        params={"code": "code", "state": state},
        cookies=browser(user, session),
        follow_redirects=False,
    )
    properties = client.get(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/properties",
        headers=auth(user),
    ).json()
    property_id = next(
        item["id"] for item in properties if item["providerPropertyId"] == "sc-domain:example.com"
    )
    selected = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/property-selection",
        json={"propertyId": property_id},
        headers=auth(user),
    )
    assert selected.status_code == 200, selected.text
    initial = selected.json()["initialSyncRun"]
    assert initial["trigger"] == "initial"
    assert (
        date.fromisoformat(initial["requestedEndDate"])
        - date.fromisoformat(initial["requestedStartDate"])
    ).days == 55

    async def inspect_and_complete() -> dict[str, object]:
        async with sessions() as db:
            run = (await db.scalars(select(SearchSyncRun))).one()
            event = (await db.scalars(select(OutboxEvent))).one()
            metadata = cast(dict[str, object], dict(event.metadata_payload))
            assert set(metadata) == {
                "runId",
                "workspaceId",
                "productId",
                "sourceId",
                "schemaVersion",
                "correlationId",
            }
            run.status = "succeeded"
            run.is_complete = True
            run.started_at = datetime.now(UTC)
            run.completed_at = datetime.now(UTC)
            source = (await db.scalars(select(SearchSource))).one()
            source.selected_at = datetime(2026, 7, 1, tzinfo=UTC)
            source.latest_successful_data_date = date(2026, 7, 20)
            await db.commit()
            return metadata

    metadata = client.portal.call(inspect_and_complete)
    assert metadata["runId"] == initial["id"]
    url = f"/v1/workspaces/{workspace.id}/products/{product.id}/search/syncs"
    first = client.post(url, json={}, headers={**auth(user), "Idempotency-Key": "manual-1"})
    replay = client.post(url, json={}, headers={**auth(user), "Idempotency-Key": "manual-1"})
    mismatch = client.post(
        url,
        json={"trigger": "scheduled"},
        headers={**auth(user), "Idempotency-Key": "manual-1"},
    )
    read = client.get(f"{url}/{first.json()['id']}", headers=auth(user))
    listed_runs = client.get(url, headers=auth(user))
    health = client.get(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/health",
        headers=auth(user),
    )

    async def seed_viewer() -> tuple[User, Workspace, Product, AuthSession]:
        return await seed(sessions, role=WorkspaceRole.viewer)

    viewer, viewer_workspace, viewer_product, _ = client.portal.call(seed_viewer)

    async def add_viewer_membership() -> None:
        async with sessions() as db:
            db.add(
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=viewer.id,
                    role=WorkspaceRole.viewer,
                    is_active=True,
                )
            )
            await db.commit()

    client.portal.call(add_viewer_membership)
    forbidden = client.post(
        url, json={}, headers={**auth(viewer), "Idempotency-Key": "viewer-manual"}
    )
    member_read = client.get(f"{url}/{first.json()['id']}", headers=auth(viewer))
    cross_workspace = client.get(
        (
            f"/v1/workspaces/{viewer_workspace.id}/products/{viewer_product.id}"
            f"/search/syncs/{first.json()['id']}"
        ),
        headers=auth(viewer),
    )
    hidden = client.get(
        f"/v1/workspaces/{uuid4()}/products/{product.id}/search/syncs/{first.json()['id']}",
        headers=auth(user),
    )

    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    assert read.status_code == 200
    assert listed_runs.json()["items"][0]["id"] == first.json()["id"]
    assert health.json()["activeSyncRunId"] == first.json()["id"]
    assert hidden.status_code == 404
    assert mismatch.status_code == 409
    assert forbidden.status_code == 403
    assert member_read.status_code == 200
    assert cross_workspace.status_code == 404
    assert "requestFingerprint" not in str(first.json())
    assert "lease" not in str(first.json()).lower()


def test_callback_denial_consumes_state_without_provider_exchange(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, provider, _ = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    _, state = connect(client, user, workspace, product)
    calls_before = list(provider.calls)

    denied = client.get(
        "/v1/integrations/search/google/callback",
        params={"error": "access_denied", "state": state},
        cookies=browser(user, session),
        follow_redirects=False,
    )
    replay = client.get(
        "/v1/integrations/search/google/callback",
        params={"error": "access_denied", "state": state},
        cookies=browser(user, session),
        follow_redirects=False,
    )

    assert denied.status_code == 307
    assert denied.headers["location"].endswith("&search=denied")
    assert replay.status_code == 400
    assert provider.calls == calls_before


def test_callback_rejects_missing_scope_and_openapi_exposes_no_credentials(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, provider, _ = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    provider.scopes = ("openid", "email")
    _, state = connect(client, user, workspace, product)

    callback = client.get(
        "/v1/integrations/search/google/callback",
        params={"code": "code", "state": state},
        cookies=browser(user, session),
        follow_redirects=False,
    )

    async def connection_count() -> int:
        async with sessions() as db:
            return int(
                await db.scalar(select(func.count()).select_from(IntegrationConnection)) or 0
            )

    search_contract = {
        path: operations
        for path, operations in client.get("/openapi.json").json()["paths"].items()
        if "/search/" in path or path.endswith("/search/connect")
    }
    rendered = str(search_contract).lower()
    assert callback.status_code == 307
    assert "search=error" in callback.headers["location"]
    assert "searchError=required_scopes_missing" in callback.headers["location"]
    assert client.portal.call(connection_count) == 0
    assert "accesstoken" not in rendered
    assert "refreshtoken" not in rendered
    assert "credentialsciphertext" not in rendered


def test_search_read_models_use_site_totals_and_deterministic_aggregates(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, provider, _ = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    selected = connect_and_select(client, user, workspace, product, session)
    source_id = UUID(cast(str, selected["id"]))
    cutoff = datetime.now(UTC).date() - timedelta(days=provider.capabilities.finalization_lag_days)

    async def seed_metrics() -> None:
        async with sessions() as db:
            source = await db.get(SearchSource, source_id)
            assert source is not None
            source.latest_successful_data_date = cutoff
            initial = (await db.scalars(select(SearchSyncRun))).one()
            initial.status = "succeeded"
            initial.is_complete = True
            initial.is_truncated = True
            initial.progress_date = initial.requested_end_date
            initial.completed_at = datetime.now(UTC)
            pages = [
                SearchPage(
                    workspace_id=workspace.id,
                    product_id=product.id,
                    source_id=source.id,
                    original_url=f"https://www.example.com/{name}",
                    normalized_url=f"https://www.example.com/{name}",
                    url_fingerprint=name,
                    match_status=status,
                    match_method=None,
                    match_policy_version="exact-url-v1",
                )
                for name, status in (("a", "unmatched"), ("b", "ambiguous"))
            ]
            queries = [
                SearchQuery(
                    workspace_id=workspace.id,
                    product_id=product.id,
                    source_id=source.id,
                    query_text=text,
                    query_fingerprint=text,
                )
                for text in ("alpha", "beta")
            ]
            db.add_all([*pages, *queries])
            await db.flush()
            for offset in range(56):
                metric_date = cutoff - timedelta(days=55 - offset)
                db.add(
                    SearchDailyMetric(
                        workspace_id=workspace.id,
                        product_id=product.id,
                        source_id=source.id,
                        metric_date=metric_date,
                        search_type="web",
                        grain="site_total",
                        clicks=1,
                        impressions=10,
                        average_position=Decimal("5"),
                    )
                )
            facts = (
                (queries[0], pages[0], cutoff, 10, 120, Decimal("4")),
                (queries[0], pages[1], cutoff, 0, 80, Decimal("8")),
                (queries[0], pages[0], cutoff - timedelta(days=28), 1, 100, Decimal("10")),
                (queries[1], pages[1], cutoff, 1, 120, Decimal("8")),
                (queries[1], pages[1], cutoff - timedelta(days=28), 10, 100, Decimal("20")),
            )
            for query, page, metric_date, clicks, impressions, position in facts:
                db.add(
                    SearchDailyMetric(
                        workspace_id=workspace.id,
                        product_id=product.id,
                        source_id=source.id,
                        query_id=query.id,
                        page_id=page.id,
                        metric_date=metric_date,
                        search_type="web",
                        grain="query_page",
                        clicks=clicks,
                        impressions=impressions,
                        average_position=position,
                    )
                )
            await db.commit()

    client.portal.call(seed_metrics)
    base = f"/v1/workspaces/{workspace.id}/products/{product.id}/search"
    baseline = client.get(
        f"{base}/baseline", params={"cutoffDate": cutoff.isoformat()}, headers=auth(user)
    )
    queries = client.get(
        f"{base}/queries",
        params={
            "startDate": (cutoff - timedelta(days=27)).isoformat(),
            "endDate": cutoff.isoformat(),
            "limit": 1,
            "offset": 0,
        },
        headers=auth(user),
    )
    pages = client.get(f"{base}/pages", headers=auth(user))

    assert baseline.status_code == 200, baseline.text
    payload = baseline.json()
    assert payload["status"] == "ready"
    assert payload["current"] == {
        "clicks": 28,
        "impressions": 280,
        "ctr": "0.1",
        "averagePosition": "5.000000",
    }
    assert [item["label"] for item in payload["views"]["topGrowingQueries"]] == ["alpha"]
    assert [item["label"] for item in payload["views"]["topDecliningQueries"]] == ["beta"]
    assert [item["label"] for item in payload["views"]["highImpressionLowCtrPages"]] == [
        "https://www.example.com/b"
    ]
    assert payload["views"]["highImpressionLowCtrPages"][0]["itemKind"] == "page"
    assert [item["label"] for item in payload["views"]["queriesWithoutRelevantPage"]] == [
        "alpha",
        "beta",
    ]
    assert queries.status_code == pages.status_code == 200
    assert queries.json()["total"] == 2
    assert queries.json()["items"][0]["text"] == "alpha"
    assert queries.json()["items"][0]["metrics"]["averagePosition"] == "5.600000"
    assert queries.json()["items"][0]["matchedPageStatus"] == "ambiguous"
    assert pages.json()["items"][0]["providerUrl"] == "https://www.example.com/b"
    assert queries.json()["isTruncated"] is True
    assert pages.json()["isTruncated"] is True
    assert queries.json()["dataQualityWarnings"] == ["query_page_data_truncated"]
    assert pages.json()["dataQualityWarnings"] == ["query_page_data_truncated"]


def test_read_validation_tenant_hiding_and_openapi_query_aliases(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, _, _ = search_client
    assert client.portal is not None
    user, workspace, product, _ = client.portal.call(seed, sessions)
    other, other_workspace, other_product, _ = client.portal.call(seed, sessions)

    async def seed_viewer() -> tuple[User, Workspace, Product, AuthSession]:
        return await seed(sessions, role=WorkspaceRole.viewer)

    viewer, viewer_workspace, viewer_product, _ = client.portal.call(seed_viewer)
    base = f"/v1/workspaces/{workspace.id}/products/{product.id}/search"

    invalid_order = client.get(
        f"{base}/queries",
        params={"startDate": "2026-07-02", "endDate": "2026-07-01"},
        headers=auth(user),
    )
    invalid_range = client.get(
        f"{base}/pages",
        params={"startDate": "2026-01-01", "endDate": "2026-07-01"},
        headers=auth(user),
    )
    invalid_page = client.get(f"{base}/queries", params={"limit": 0}, headers=auth(user))
    hidden = client.get(
        f"/v1/workspaces/{other_workspace.id}/products/{product.id}/search/health",
        headers=auth(other),
    )
    member_pending = client.get(
        f"/v1/workspaces/{other_workspace.id}/products/{other_product.id}/search/baseline",
        headers=auth(other),
    )
    forbidden_unlink = client.delete(
        f"/v1/workspaces/{viewer_workspace.id}/products/{viewer_product.id}/search/source",
        headers=auth(viewer),
    )
    forbidden_revoke = client.delete(
        f"/v1/workspaces/{viewer_workspace.id}/search/connections/google",
        headers=auth(viewer),
    )
    operation = client.get("/openapi.json").json()["paths"][
        "/v1/workspaces/{workspace_id}/products/{product_id}/search/queries"
    ]["get"]
    parameter_names = {item["name"] for item in operation["parameters"]}

    assert invalid_order.status_code == invalid_range.status_code == invalid_page.status_code == 400
    assert hidden.status_code == 404
    assert forbidden_unlink.status_code == forbidden_revoke.status_code == 403
    assert member_pending.status_code == 200
    assert member_pending.json()["status"] == "pending"
    assert {"startDate", "endDate", "limit", "offset"}.issubset(parameter_names)
    assert "credential" not in str(operation).lower()

    readiness = client.get(f"{base}/readiness", headers=auth(user))
    assert readiness.status_code == 200
    assert readiness.json() == {
        "requirementLevel": "recommended",
        "reducedCapabilityAllowed": True,
        "setupComplete": False,
        "baselineUsable": False,
        "blockingReasons": [],
        "capabilityReductions": ["search_dependent_recommendations_unavailable"],
        "healthStatus": "not_connected",
        "activeSyncRunId": None,
    }


def test_product_unlink_and_workspace_revoke_are_scoped_and_idempotent(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, provider, vault = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    selected = connect_and_select(client, user, workspace, product, session)
    source_id = UUID(cast(str, selected["id"]))
    initial_run = cast(dict[str, object], selected["initialSyncRun"])
    run_id = UUID(cast(str, initial_run["id"]))
    product_url = f"/v1/workspaces/{workspace.id}/products/{product.id}/search/source"

    async def seed_other_product_source() -> UUID:
        async with sessions() as db:
            source = await db.get(SearchSource, source_id)
            assert source is not None
            other_product = Product(workspace_id=workspace.id, user_id=user.id, name="Other")
            db.add(other_product)
            await db.flush()
            website = WebsiteSource(
                workspace_id=workspace.id,
                product_id=other_product.id,
                base_url="https://other.example.com/",
                normalized_base_url="https://other.example.com/",
                status=WebsiteSourceStatus.active,
                created_by=user.id,
            )
            db.add(website)
            await db.flush()
            other_source = SearchSource(
                workspace_id=workspace.id,
                product_id=other_product.id,
                integration_connection_id=source.integration_connection_id,
                property_id=source.property_id,
                website_source_id=website.id,
                provider=source.provider,
                status="active",
                selected_at=datetime.now(UTC),
                created_by=user.id,
            )
            db.add(other_source)
            await db.commit()
            return other_source.id

    other_source_id = client.portal.call(seed_other_product_source)

    unlinked = client.delete(product_url, headers=auth(user))
    repeat_unlink = client.delete(product_url, headers=auth(user))

    async def inspect_unlink() -> tuple[str, str, str, str | None, str]:
        async with sessions() as db:
            source = await db.get(SearchSource, source_id)
            other_source = await db.get(SearchSource, other_source_id)
            connection = (await db.scalars(select(IntegrationConnection))).one()
            run = (await db.scalars(select(SearchSyncRun))).one()
            assert source is not None and other_source is not None
            return (
                source.status,
                connection.status,
                run.status,
                run.lease_owner,
                other_source.status,
            )

    assert unlinked.status_code == repeat_unlink.status_code == 204
    assert client.portal.call(inspect_unlink) == (
        "disconnected",
        "active",
        "cancelled",
        None,
        "active",
    )
    assert not any(call[0] == "revoke" for call in provider.calls)

    revoke_url = f"/v1/workspaces/{workspace.id}/search/connections/google"
    revoked = client.delete(revoke_url, headers=auth(user))
    repeated = client.delete(revoke_url, headers=auth(user))
    health = client.get(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/search/health",
        headers=auth(user),
    )

    async def inspect_revoke() -> tuple[str, str | None, str | None, list[str]]:
        async with sessions() as db:
            connection = (await db.scalars(select(IntegrationConnection))).one()
            sources = list(await db.scalars(select(SearchSource).order_by(SearchSource.id)))
            return (
                connection.status,
                connection.credentials_ciphertext,
                connection.credential_key_version,
                sorted(source.status for source in sources),
            )

    assert revoked.status_code == repeated.status_code == 204
    assert client.portal.call(inspect_revoke) == (
        "revoked",
        None,
        None,
        ["disconnected", "disconnected"],
    )
    assert sum(call[0] == "revoke" for call in provider.calls) == 1
    assert health.json()["state"] == "revoked"
    assert health.json()["canReconnect"] is True
    assert health.json()["canRevokeWorkspaceConnection"] is False
    calls_before_processing = list(provider.calls)
    service = SearchSyncService(
        sessions=sessions,
        providers=SearchProviderRegistry([provider]),
        vault=vault,
    )
    assert client.portal.call(service.process, run_id)["status"] == "cancelled"
    assert provider.calls == calls_before_processing


@pytest.mark.parametrize("failure", ["remote", "decrypt"])
def test_workspace_revoke_failure_still_invalidates_locally(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
    failure: str,
) -> None:
    client, sessions, provider, _ = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    connect_and_select(client, user, workspace, product, session)
    provider.fail_revoke = failure == "remote"

    if failure == "decrypt":

        async def corrupt_credentials() -> None:
            async with sessions() as db:
                connection = (await db.scalars(select(IntegrationConnection))).one()
                connection.credentials_ciphertext = "not-valid-ciphertext"
                await db.commit()

        client.portal.call(corrupt_credentials)

    response = client.delete(
        f"/v1/workspaces/{workspace.id}/search/connections/google",
        headers=auth(user),
    )

    async def inspect() -> tuple[str, str | None, list[str], list[str]]:
        async with sessions() as db:
            connection = (await db.scalars(select(IntegrationConnection))).one()
            sources = list(await db.scalars(select(SearchSource)))
            runs = list(await db.scalars(select(SearchSyncRun)))
            return (
                connection.status,
                connection.credentials_ciphertext,
                [source.status for source in sources],
                [run.status for run in runs],
            )

    assert response.status_code == 204
    assert client.portal.call(inspect) == ("revoked", None, ["disconnected"], ["cancelled"])


def test_browser_telemetry_validates_resources_and_is_idempotent(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, _, _ = search_client
    assert client.portal is not None
    user, workspace, product, session = client.portal.call(seed, sessions)
    connect_and_select(client, user, workspace, product, session)
    other_user, other_workspace, other_product, _ = client.portal.call(seed, sessions)
    url = browser_event_url(workspace, product)

    baseline = {
        "eventName": "search_baseline_viewed",
        "resourceId": str(product.id),
    }
    first = client.post(url, json=baseline, headers={**auth(user), "Idempotency-Key": "baseline"})
    duplicate = client.post(
        url, json=baseline, headers={**auth(user), "Idempotency-Key": "baseline"}
    )
    compatibility = client.post(
        url,
        json={"eventName": "compatibility_route_viewed", "resourceId": str(product.id)},
        headers={**auth(user), "Idempotency-Key": "compatibility"},
    )
    nonexistent = client.post(
        url,
        json={"eventName": "search_baseline_viewed", "resourceId": str(uuid4())},
        headers={**auth(user), "Idempotency-Key": "missing"},
    )
    cross_tenant = client.post(
        url,
        json={
            "eventName": "compatibility_route_viewed",
            "resourceId": str(other_product.id),
        },
        headers={**auth(user), "Idempotency-Key": "other-tenant"},
    )
    invisible = client.post(
        url,
        json={"eventName": "compatibility_route_viewed", "resourceId": str(product.id)},
        headers={**auth(other_user), "Idempotency-Key": "invisible"},
    )

    assert first.status_code == 200, first.text
    assert first.json()["duplicate"] is False
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json() == {**first.json(), "duplicate": True}
    assert compatibility.status_code == 200, compatibility.text
    assert nonexistent.status_code == 404
    assert nonexistent.json()["code"] == "event_resource_not_found"
    assert cross_tenant.status_code == 404
    assert invisible.status_code == 404
    assert other_workspace.id != workspace.id

    async def event_count() -> int:
        async with sessions() as db:
            return int(await db.scalar(select(func.count()).select_from(ProductEvent)) or 0)

    assert client.portal.call(event_count) == 4


def test_browser_telemetry_rejects_client_fabricated_outcomes(
    search_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        GoogleSearchFake,
        DeterministicFakeIntegrationCredentialVault,
    ],
) -> None:
    client, sessions, _, _ = search_client
    assert client.portal is not None
    user, workspace, product, _ = client.portal.call(seed, sessions)
    url = browser_event_url(workspace, product)

    outcome_event = client.post(
        url,
        json={"eventName": "measurement_completed", "resourceId": str(product.id)},
        headers={**auth(user), "Idempotency-Key": "fabricated-event"},
    )
    outcome_metric = client.post(
        url,
        json={
            "eventName": "compatibility_route_viewed",
            "resourceId": str(product.id),
            "outcome": "improved",
        },
        headers={**auth(user), "Idempotency-Key": "fabricated-metric"},
    )

    assert outcome_event.status_code == 422
    assert outcome_metric.status_code == 422
