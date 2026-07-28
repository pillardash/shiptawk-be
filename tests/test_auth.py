from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from datetime import timedelta
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token, decode_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.modules.identity.models.oauth import AuthSession, OAuthIdentity
from app.modules.identity.models.users import User
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import Workspace, WorkspaceMembership
from app.services.oauth import FakeOAuthProvider, OAuthIdentityData, OAuthProviderRegistry


def run_with_db[T](client: TestClient, operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def execute() -> T:
        app = cast(FastAPI, client.app)
        async with app.state.testing_session() as db:
            return await operation(db)

    assert client.portal is not None
    return client.portal.call(execute)


@pytest.fixture
def auth_client() -> Generator[tuple[TestClient, FakeOAuthProvider], None, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    provider = FakeOAuthProvider(
        identity=OAuthIdentityData(
            subject="12345",
            email="user@example.com",
            email_verified=True,
            username="octocat",
            display_name="Octo Cat",
            avatar_url="https://avatars.example/octocat",
        )
    )
    app = create_app()
    registry = OAuthProviderRegistry()
    registry.register(provider)
    app.state.oauth_providers = registry
    app.state.testing_session = sessions
    app.dependency_overrides[get_db] = override_get_db

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, provider
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


def complete_oauth(client: TestClient, *, state_suffix: str = "") -> None:
    authorize = client.get(
        "/api/v1/auth/browser/oauth/github/authorize",
        params={"returnPath": f"/dashboard{state_suffix}"},
        follow_redirects=False,
    )
    assert authorize.status_code == 307
    state = authorize.headers["location"].split("state=")[1].split("&")[0]
    callback = client.get(
        "/api/v1/auth/browser/oauth/github/callback",
        params={"code": "deterministic-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 307
    assert callback.headers["location"].endswith(f"/dashboard{state_suffix}")


async def model_count(db: AsyncSession, model: type[object]) -> int:
    return int((await db.scalar(select(func.count()).select_from(model))) or 0)


def test_password_auth_routes_are_not_public(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    openapi = client.get("/openapi.json").json()

    for path in (
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/api/v1/auth/change-password",
    ):
        assert path not in openapi["paths"]


def test_browser_provider_discovery_and_validation(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client

    providers = client.get("/api/v1/auth/browser/providers")
    missing = client.get("/api/v1/auth/browser/oauth/google/authorize", follow_redirects=False)
    invalid_return = client.get(
        "/api/v1/auth/browser/oauth/github/authorize",
        params={"returnPath": "https://attacker.example"},
        follow_redirects=False,
    )

    assert providers.status_code == 200
    assert providers.json() == [{"name": "github", "displayName": "GitHub"}]
    assert providers.headers["cache-control"] == "no-store"
    assert missing.status_code == 400
    assert missing.json()["code"] == "provider_not_configured"
    assert invalid_return.status_code == 400
    assert invalid_return.json()["code"] == "invalid_return_path"


def test_oauth_state_is_one_time_and_browser_session_requires_cookie(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    authorize = client.get("/api/v1/auth/browser/oauth/github/authorize", follow_redirects=False)
    state = authorize.headers["location"].split("state=")[1].split("&")[0]
    first = client.get(
        "/api/v1/auth/browser/oauth/github/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )
    second = client.get(
        "/api/v1/auth/browser/oauth/github/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )

    assert first.status_code == 307
    assert second.status_code == 400
    assert second.json()["code"] == "invalid_oauth_state"
    client.cookies.clear()
    missing = client.get("/api/v1/auth/browser/session")
    assert missing.status_code == 401
    assert missing.json()["code"] == "missing_session"


def test_access_token_round_trip() -> None:
    assert decode_access_token(create_access_token("subject")) == "subject"


def test_oauth_callback_provisions_account_workspace_and_session_atomically(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    complete_oauth(client)

    response = client.get("/api/v1/auth/browser/session")
    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["email"] == "user@example.com"
    assert payload["currentWorkspace"]["name"] == "Octo Cat's workspace"
    assert payload["currentWorkspace"]["role"] == "owner"
    assert payload["linkedProviders"] == ["github"]
    assert "accessToken" not in payload
    assert "refreshToken" not in payload
    assert run_with_db(client, lambda db: model_count(db, User)) == 1
    assert run_with_db(client, lambda db: model_count(db, OAuthIdentity)) == 1
    assert run_with_db(client, lambda db: model_count(db, Workspace)) == 1
    assert run_with_db(client, lambda db: model_count(db, WorkspaceMembership)) == 1
    assert run_with_db(client, lambda db: model_count(db, AuthSession)) == 1


def test_oauth_callback_rolls_back_all_provisioning_when_session_creation_fails(
    auth_client: tuple[TestClient, FakeOAuthProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = auth_client

    async def fail_session_creation(*args: object, **kwargs: object) -> object:
        raise RuntimeError("session storage failed")

    monkeypatch.setattr(
        "app.modules.identity.services.browser_oauth.create_refresh_session_record",
        fail_session_creation,
    )
    authorize = client.get("/api/v1/auth/browser/oauth/github/authorize", follow_redirects=False)
    state = authorize.headers["location"].split("state=")[1].split("&")[0]

    with pytest.raises(RuntimeError, match="session storage failed"):
        client.get(
            "/api/v1/auth/browser/oauth/github/callback",
            params={"code": "code", "state": state},
            follow_redirects=False,
        )

    assert run_with_db(client, lambda db: model_count(db, User)) == 0
    assert run_with_db(client, lambda db: model_count(db, OAuthIdentity)) == 0
    assert run_with_db(client, lambda db: model_count(db, Workspace)) == 0
    assert run_with_db(client, lambda db: model_count(db, WorkspaceMembership)) == 0
    assert run_with_db(client, lambda db: model_count(db, AuthSession)) == 0


def test_existing_identity_login_does_not_create_duplicate_workspace(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    complete_oauth(client)
    complete_oauth(client, state_suffix="?again=1")

    assert run_with_db(client, lambda db: model_count(db, User)) == 1
    assert run_with_db(client, lambda db: model_count(db, Workspace)) == 1
    assert run_with_db(client, lambda db: model_count(db, WorkspaceMembership)) == 1
    assert run_with_db(client, lambda db: model_count(db, AuthSession)) == 2


def test_browser_refresh_rotates_session_and_logout_revokes_it(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    complete_oauth(client)
    original_refresh = client.cookies["refresh_token"]
    csrf = client.cookies["csrf_token"]

    bad_origin = client.post(
        "/api/v1/auth/browser/refresh",
        headers={"Origin": "https://attacker.example", "X-CSRF-Token": csrf},
    )
    assert bad_origin.status_code == 400
    assert bad_origin.json()["code"] == "invalid_origin"

    refreshed = client.post(
        "/api/v1/auth/browser/refresh",
        headers={"Origin": "http://localhost:3000", "X-CSRF-Token": csrf},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["currentWorkspace"]["role"] == "owner"
    assert client.cookies["refresh_token"] != original_refresh

    new_csrf = client.cookies["csrf_token"]
    logged_out = client.post(
        "/api/v1/auth/browser/logout",
        headers={"Origin": "http://localhost:3000", "X-CSRF-Token": new_csrf},
    )
    assert logged_out.status_code == 200
    assert logged_out.json() == {"message": "Logged out successfully."}
    assert "access_token" not in client.cookies


@pytest.mark.parametrize("access_state", ["missing", "expired"])
def test_browser_refresh_uses_opaque_refresh_session_without_valid_access_jwt(
    auth_client: tuple[TestClient, FakeOAuthProvider], access_state: str
) -> None:
    client, _ = auth_client
    complete_oauth(client)
    csrf = client.cookies["csrf_token"]
    if access_state == "missing":
        del client.cookies["access_token"]
    else:
        client.cookies.set(
            "access_token",
            create_access_token("invalid-subject", expires_delta=timedelta(seconds=-1)),
        )

    response = client.post(
        "/api/v1/auth/browser/refresh",
        headers={"Origin": "http://localhost:3000", "X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "user@example.com"


def test_logout_is_idempotent_without_authentication_and_always_clears_cookies(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    client.cookies.set("access_token", "expired", path="/")
    client.cookies.set("refresh_token", "revoked-or-invalid", path="/api/v1/auth/browser")
    client.cookies.set("csrf_token", "stale", path="/")

    response = client.post("/api/v1/auth/browser/logout")

    assert response.status_code == 200
    cleared = response.headers.get_list("set-cookie")
    assert all(
        any(cookie.startswith(f"{name}=") and "Max-Age=0" in cookie for cookie in cleared)
        for name in ("access_token", "refresh_token", "csrf_token")
    )


def test_active_logout_requires_refresh_session_bound_csrf_without_access_jwt(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    complete_oauth(client)
    del client.cookies["access_token"]

    missing_csrf = client.post("/api/v1/auth/browser/logout")
    assert missing_csrf.status_code == 400
    assert missing_csrf.json()["code"] == "invalid_origin"


def test_active_logout_requires_access_session_bound_csrf_without_refresh_cookie(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    complete_oauth(client)
    del client.cookies["refresh_token"]

    missing_csrf = client.post("/api/v1/auth/browser/logout")

    assert missing_csrf.status_code == 400
    assert missing_csrf.json()["code"] == "invalid_origin"


async def soft_delete_account_state(db: AsyncSession) -> tuple[User, OAuthIdentity, AuthSession]:
    return (
        (await db.scalars(select(User))).one(),
        (await db.scalars(select(OAuthIdentity))).one(),
        (await db.scalars(select(AuthSession))).one(),
    )


def test_account_deletion_soft_deletes_user_revokes_identity_and_sessions(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    complete_oauth(client)
    csrf = client.cookies["csrf_token"]

    response = client.delete(
        "/api/v1/auth/browser/account",
        headers={"Origin": "http://localhost:3000", "X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert not client.cookies
    user, identity, session = run_with_db(client, soft_delete_account_state)
    assert user.deleted_at is not None
    assert user.is_active is False
    assert identity.deleted_at is not None
    assert session.revoked_at is not None


def test_soft_deleted_oauth_identity_is_not_automatically_restored(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    complete_oauth(client)
    csrf = client.cookies["csrf_token"]
    assert (
        client.delete(
            "/api/v1/auth/browser/account",
            headers={"Origin": "http://localhost:3000", "X-CSRF-Token": csrf},
        ).status_code
        == 200
    )

    authorize = client.get("/api/v1/auth/browser/oauth/github/authorize", follow_redirects=False)
    state = authorize.headers["location"].split("state=")[1].split("&")[0]
    response = client.get(
        "/api/v1/auth/browser/oauth/github/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert response.json()["code"] == "deleted_identity"
    assert run_with_db(client, lambda db: model_count(db, User)) == 1


def test_oauth_callback_uses_canonical_public_backend_url_not_request_host(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client

    response = client.get(
        "/api/v1/auth/browser/oauth/github/authorize",
        headers={"Host": "attacker.example", "X-Forwarded-Host": "attacker.example"},
        follow_redirects=False,
    )

    assert response.status_code == 307
    canonical_callback = (
        "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fapi%2Fv1%2Fauth%2Fbrowser%2F"
        "oauth%2Fgithub%2Fcallback"
    )
    assert canonical_callback in response.headers["location"]


async def deactivate_user(db: AsyncSession) -> None:
    user = (await db.scalars(select(User))).one()
    user.is_active = False
    await db.commit()


def test_oauth_callback_rejects_inactive_existing_user(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    complete_oauth(client)
    run_with_db(client, deactivate_user)

    authorize = client.get("/api/v1/auth/browser/oauth/github/authorize", follow_redirects=False)
    state = authorize.headers["location"].split("state=")[1].split("&")[0]
    response = client.get(
        "/api/v1/auth/browser/oauth/github/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert response.json()["code"] == "inactive_user"
    assert run_with_db(client, lambda db: model_count(db, AuthSession)) == 1


async def point_session_at_cross_workspace(db: AsyncSession) -> None:
    session = (
        await db.scalars(select(AuthSession).order_by(AuthSession.created_at.desc()))
    ).first()
    assert session is not None
    other = Workspace(name="Other")
    db.add(other)
    await db.flush()
    session.current_workspace_id = other.id
    await db.commit()


def test_browser_session_rejects_cross_workspace_session_binding(
    auth_client: tuple[TestClient, FakeOAuthProvider],
) -> None:
    client, _ = auth_client
    complete_oauth(client)
    run_with_db(client, point_session_at_cross_workspace)

    response = client.get("/api/v1/auth/browser/session")

    assert response.status_code == 403
    assert response.json()["code"] == "workspace_access_denied"


def test_no_email_auto_linking(auth_client: tuple[TestClient, FakeOAuthProvider]) -> None:
    client, provider = auth_client
    complete_oauth(client)
    provider.identity = OAuthIdentityData(
        subject="different-provider-subject",
        email="user@example.com",
        email_verified=True,
        username="second",
    )
    complete_oauth(client)

    assert run_with_db(client, lambda db: model_count(db, User)) == 2
    assert run_with_db(client, lambda db: model_count(db, Workspace)) == 2


def test_workspace_roles_include_product_roles() -> None:
    assert {role.value for role in WorkspaceRole} == {
        "owner",
        "admin",
        "editor",
        "reviewer",
        "viewer",
    }
