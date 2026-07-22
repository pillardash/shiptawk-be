from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token, hash_token
from app.db.base import Base
from app.db.session import get_db
from app.domains.auth.models import AuthSession
from app.domains.integrations.models import IntegrationConnection, IntegrationOAuthTransaction
from app.domains.integrations.x_oauth import (
    FakeIntegrationCredentialCipher,
    FakeXOAuthProvider,
    HttpXOAuthProvider,
    XOAuthGrant,
)
from app.domains.users.models import User
from app.domains.workspaces.models import Workspace, WorkspaceMembership, WorkspaceRole
from app.main import create_app


def x_grant(account_id: str = "x-account-1") -> XOAuthGrant:
    return XOAuthGrant(
        account_id=account_id,
        username="shiptawk_test",
        access_token="access-secret",
        refresh_token="refresh-secret",
        scopes=["tweet.read", "tweet.write", "users.read", "offline.access"],
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )


@pytest.mark.asyncio
async def test_http_x_oauth_adapter_uses_pkce_and_parses_sanitized_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/2/oauth2/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "provider-access-secret",
                    "refresh_token": "provider-refresh-secret",
                    "expires_in": 7200,
                    "scope": "tweet.read tweet.write users.read offline.access",
                },
            )
        return httpx.Response(200, json={"data": {"id": "account-1", "username": "shiptawk"}})

    provider = HttpXOAuthProvider(
        client_id="client-id",
        client_secret="client-secret",
        scopes=["tweet.read", "tweet.write", "users.read", "offline.access"],
        http_transport=httpx.MockTransport(handler),
    )

    grant = await provider.exchange_code(
        code="one-time-code", code_verifier="pkce-verifier", redirect_uri="https://api.test/cb"
    )

    assert grant.account_id == "account-1"
    assert grant.username == "shiptawk"
    assert grant.scopes == ["tweet.read", "tweet.write", "users.read", "offline.access"]
    assert [request.url.path for request in requests] == ["/2/oauth2/token", "/2/users/me"]
    assert b"code_verifier=pkce-verifier" in requests[0].content
    assert requests[1].headers["authorization"] == "Bearer provider-access-secret"


async def seed_identity_async(
    sessions: async_sessionmaker[AsyncSession], role: WorkspaceRole = WorkspaceRole.owner
) -> tuple[User, Workspace, Workspace, AuthSession]:
    async with sessions() as db:
        user = User(email=f"x-oauth-{uuid4()}@example.com")
        workspace = Workspace(name="X workspace")
        other = Workspace(name="Other workspace")
        db.add_all([user, workspace, other])
        await db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=role,
                is_active=True,
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
        return user, workspace, other, session


def browser_cookie(user: User, session: AuthSession) -> dict[str, str]:
    return {"access_token": create_access_token(str(user.id), session_id=str(session.id))}


@pytest.fixture
def x_oauth_client() -> Generator[
    tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        FakeXOAuthProvider,
        FakeIntegrationCredentialCipher,
    ],
    None,
    None,
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    provider = FakeXOAuthProvider(grant=x_grant())
    cipher = FakeIntegrationCredentialCipher()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    app = create_app()
    app.state.x_oauth_provider = provider
    app.state.integration_credential_cipher = cipher
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, sessions, provider, cipher
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


def start_x_oauth(
    client: TestClient,
    workspace: Workspace,
    cookies: dict[str, str],
    return_path: str = "/settings",
) -> str:
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/x/authorize",
        params={"returnPath": return_path},
        cookies=cookies,
        follow_redirects=False,
    )
    assert response.status_code == 307
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"][0].endswith(f"/workspaces/{workspace.id}/x/callback")
    return query["state"][0]


def test_x_oauth_connect_encrypts_credentials_and_exposes_only_safe_connection_fields(
    x_oauth_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        FakeXOAuthProvider,
        FakeIntegrationCredentialCipher,
    ],
) -> None:
    client, sessions, provider, cipher = x_oauth_client
    assert client.portal is not None
    user, workspace, _, session = client.portal.call(seed_identity_async, sessions)
    cookies = browser_cookie(user, session)
    state = start_x_oauth(client, workspace, cookies)

    callback = client.get(
        f"/api/v1/workspaces/{workspace.id}/x/callback",
        params={"code": "provider-code", "state": state},
        cookies=cookies,
        follow_redirects=False,
    )
    listed = client.get(f"/api/v1/workspaces/{workspace.id}/connections", cookies=cookies)
    status = client.get(f"/api/v1/workspaces/{workspace.id}/x/connection", cookies=cookies)

    assert callback.status_code == 307
    assert callback.headers["location"] == "http://localhost:3000/settings?x=connected"
    assert len(provider.exchanges) == 1
    assert provider.exchanges[0].code == "provider-code"
    assert provider.exchanges[0].code_verifier
    assert cipher.values == [
        {
            "accessToken": "access-secret",
            "refreshToken": "refresh-secret",
        }
    ]
    assert listed.status_code == status.status_code == 200
    assert listed.json()[0] == {
        "id": listed.json()[0]["id"],
        "provider": "x",
        "accountId": "x-account-1",
        "username": "shiptawk_test",
        "status": "active",
        "scopes": ["tweet.read", "tweet.write", "users.read", "offline.access"],
        "tokenExpiresAt": listed.json()[0]["tokenExpiresAt"],
        "connectedAt": listed.json()[0]["connectedAt"],
    }
    assert status.json()["connected"] is True
    assert "secret" not in str(listed.json())

    async def persisted() -> tuple[str, str, int]:
        async with sessions() as db:
            connection = (await db.scalars(select(IntegrationConnection))).one()
            transactions = int(
                (await db.scalar(select(func.count()).select_from(IntegrationOAuthTransaction)))
                or 0
            )
            return (
                connection.credentials_ciphertext,
                connection.credential_key_version,
                transactions,
            )

    ciphertext, key_version, transactions = client.portal.call(persisted)
    assert (ciphertext, key_version) == ("encrypted:1", "fake-v1")
    assert transactions == 1


def test_x_oauth_state_is_one_time_actor_and_workspace_bound(
    x_oauth_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        FakeXOAuthProvider,
        FakeIntegrationCredentialCipher,
    ],
) -> None:
    client, sessions, provider, _ = x_oauth_client
    assert client.portal is not None
    user, workspace, other, session = client.portal.call(seed_identity_async, sessions)
    cookies = browser_cookie(user, session)
    state = start_x_oauth(client, workspace, cookies)

    wrong_workspace = client.get(
        f"/api/v1/workspaces/{other.id}/x/callback",
        params={"code": "code", "state": state},
        cookies=cookies,
        follow_redirects=False,
    )
    first = client.get(
        f"/api/v1/workspaces/{workspace.id}/x/callback",
        params={"code": "code", "state": state},
        cookies=cookies,
        follow_redirects=False,
    )
    replay = client.get(
        f"/api/v1/workspaces/{workspace.id}/x/callback",
        params={"code": "code", "state": state},
        cookies=cookies,
        follow_redirects=False,
    )

    assert wrong_workspace.status_code == 403
    assert wrong_workspace.json()["code"] == "workspace_access_denied"
    assert first.status_code == 307
    assert replay.status_code == 400
    assert replay.json()["code"] == "invalid_oauth_state"
    assert len(provider.exchanges) == 1


def test_x_oauth_rejects_unsafe_redirect_and_requires_workspace_admin(
    x_oauth_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        FakeXOAuthProvider,
        FakeIntegrationCredentialCipher,
    ],
) -> None:
    client, sessions, _, _ = x_oauth_client
    assert client.portal is not None
    owner, owner_workspace, _, owner_session = client.portal.call(seed_identity_async, sessions)
    user, workspace, _, session = client.portal.call(
        seed_identity_async, sessions, WorkspaceRole.viewer
    )
    cookies = browser_cookie(user, session)

    unsafe = client.get(
        f"/api/v1/workspaces/{owner_workspace.id}/x/authorize",
        params={"returnPath": "//attacker.example/steal"},
        cookies=browser_cookie(owner, owner_session),
        follow_redirects=False,
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/x/authorize",
        params={"returnPath": "/settings"},
        cookies=cookies,
        follow_redirects=False,
    )

    assert unsafe.status_code == 400
    assert unsafe.json()["code"] == "invalid_return_path"
    assert forbidden.status_code == 403


def test_x_oauth_denial_consumes_state_and_redirects_without_provider_call(
    x_oauth_client: tuple[
        TestClient,
        async_sessionmaker[AsyncSession],
        FakeXOAuthProvider,
        FakeIntegrationCredentialCipher,
    ],
) -> None:
    client, sessions, provider, _ = x_oauth_client
    assert client.portal is not None
    user, workspace, _, session = client.portal.call(seed_identity_async, sessions)
    cookies = browser_cookie(user, session)
    state = start_x_oauth(client, workspace, cookies, "/onboarding?step=connect-twitter")

    denied = client.get(
        f"/api/v1/workspaces/{workspace.id}/x/callback",
        params={"error": "access_denied", "state": state},
        cookies=cookies,
        follow_redirects=False,
    )
    replay = client.get(
        f"/api/v1/workspaces/{workspace.id}/x/callback",
        params={"error": "access_denied", "state": state},
        cookies=cookies,
        follow_redirects=False,
    )

    assert denied.status_code == 307
    assert denied.headers["location"] == (
        "http://localhost:3000/onboarding?step=connect-twitter&x=denied"
    )
    assert replay.status_code == 400
    assert provider.exchanges == []
