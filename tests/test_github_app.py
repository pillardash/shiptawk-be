from collections.abc import AsyncGenerator, Generator
from typing import cast
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.modules.integrations.providers.github import (
    FakeGitHubAppProvider,
    GitHubAppError,
    GitHubInstallation,
    GitHubRepository,
    HttpGitHubAppProvider,
)
from app.modules.workspaces.models import WorkspaceMembership, WorkspaceRole
from app.services.oauth import FakeOAuthProvider, OAuthIdentityData, OAuthProviderRegistry


@pytest.fixture
def github_app_client() -> Generator[tuple[TestClient, FakeGitHubAppProvider], None, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    app = create_app()
    oauth = FakeOAuthProvider(
        identity=OAuthIdentityData(
            subject="12345", email="owner@example.com", email_verified=True, username="owner"
        )
    )
    registry = OAuthProviderRegistry()
    registry.register(oauth)
    github = FakeGitHubAppProvider(
        installation=GitHubInstallation(id=42, account_login="acme"),
        repositories=[
            GitHubRepository(
                id=100,
                name="api",
                full_name="acme/api",
                private=True,
                default_branch="main",
                language="Python",
                description="API",
            )
        ],
    )
    app.state.oauth_providers = registry
    app.state.github_app = github
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
        authorize = client.get(
            "/api/v1/auth/browser/oauth/github/authorize", follow_redirects=False
        )
        state = authorize.headers["location"].split("state=")[1].split("&")[0]
        callback = client.get(
            "/api/v1/auth/browser/oauth/github/callback",
            params={"code": "code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 307
        yield client, github
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


def workspace_id(client: TestClient) -> str:
    return cast(str, client.get("/api/v1/auth/browser/session").json()["currentWorkspace"]["id"])


def mutation_headers(client: TestClient) -> dict[str, str]:
    return {
        "Origin": "http://localhost:3000",
        "X-CSRF-Token": client.cookies["csrf_token"],
    }


def test_attach_sync_and_toggle_workspace_repository(
    github_app_client: tuple[TestClient, FakeGitHubAppProvider],
) -> None:
    client, github = github_app_client
    workspace = workspace_id(client)

    attached = client.post(
        f"/api/v1/workspaces/{workspace}/github/installation",
        json={"installationId": 42},
        headers=mutation_headers(client),
    )
    synced = client.post(
        f"/api/v1/workspaces/{workspace}/repositories/sync",
        headers=mutation_headers(client),
    )
    listed = client.get(f"/api/v1/workspaces/{workspace}/repositories")
    repo_id = listed.json()[0]["id"]
    tracked = client.patch(
        f"/api/v1/workspaces/{workspace}/repositories/{repo_id}/tracking",
        json={"isTracked": True},
        headers=mutation_headers(client),
    )

    assert attached.status_code == 200
    assert attached.json() == {
        "installationId": 42,
        "accountLogin": "acme",
        "status": "active",
    }
    assert synced.status_code == 200
    assert synced.json()[0]["isTracked"] is False
    assert listed.json()[0]["repoFullName"] == "acme/api"
    assert tracked.json()["isTracked"] is True
    assert "token" not in str(attached.json()).lower()
    assert github.verify_calls == 1
    assert github.list_calls == 1


def test_repository_detail_branch_and_changelog_settings(
    github_app_client: tuple[TestClient, FakeGitHubAppProvider],
) -> None:
    client, _ = github_app_client
    workspace = workspace_id(client)
    headers = mutation_headers(client)
    client.post(
        f"/api/v1/workspaces/{workspace}/github/installation",
        json={"installationId": 42},
        headers=headers,
    )
    repo_id = client.post(
        f"/api/v1/workspaces/{workspace}/repositories/sync", headers=headers
    ).json()[0]["id"]

    branch = client.patch(
        f"/api/v1/workspaces/{workspace}/repositories/{repo_id}",
        json={"trackedBranch": "release"},
        headers=headers,
    )
    digest = client.patch(
        f"/api/v1/workspaces/{workspace}/repositories/{repo_id}/changelog-digest-settings",
        json={"enabled": True, "frequency": "monthly"},
        headers=headers,
    )
    detail = client.get(f"/api/v1/workspaces/{workspace}/repositories/{repo_id}")

    assert branch.status_code == 200
    assert branch.json()["trackedBranch"] == "release"
    assert digest.status_code == 200
    assert digest.json()["changelogDigestEnabled"] is True
    assert digest.json()["changelogDigestFrequency"] == "monthly"
    assert detail.status_code == 200
    assert detail.json()["id"] == repo_id
    assert detail.json()["workspaceId"] == workspace
    assert detail.json()["createdAt"] is not None


def test_repository_detail_mutations_validate_and_enforce_workspace_scope(
    github_app_client: tuple[TestClient, FakeGitHubAppProvider],
) -> None:
    client, _ = github_app_client
    workspace = workspace_id(client)
    headers = mutation_headers(client)
    client.post(
        f"/api/v1/workspaces/{workspace}/github/installation",
        json={"installationId": 42},
        headers=headers,
    )
    repo_id = client.post(
        f"/api/v1/workspaces/{workspace}/repositories/sync", headers=headers
    ).json()[0]["id"]
    other_workspace = "00000000-0000-0000-0000-000000000001"

    invalid_branch = client.patch(
        f"/api/v1/workspaces/{workspace}/repositories/{repo_id}",
        json={"trackedBranch": " "},
        headers=headers,
    )
    invalid_frequency = client.patch(
        f"/api/v1/workspaces/{workspace}/repositories/{repo_id}/changelog-digest-settings",
        json={"enabled": True, "frequency": "daily"},
        headers=headers,
    )
    cross_workspace_read = client.get(
        f"/api/v1/workspaces/{other_workspace}/repositories/{repo_id}"
    )
    cross_workspace_write = client.patch(
        f"/api/v1/workspaces/{other_workspace}/repositories/{repo_id}",
        json={"trackedBranch": "main"},
        headers=headers,
    )

    assert invalid_branch.status_code == 422
    assert invalid_frequency.status_code == 422
    assert cross_workspace_read.status_code == 403
    assert cross_workspace_write.status_code == 403


def test_attach_and_sync_are_idempotent(
    github_app_client: tuple[TestClient, FakeGitHubAppProvider],
) -> None:
    client, _ = github_app_client
    workspace = workspace_id(client)
    headers = mutation_headers(client)

    for _ in range(2):
        assert (
            client.post(
                f"/api/v1/workspaces/{workspace}/github/installation",
                json={"installationId": 42},
                headers=headers,
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/workspaces/{workspace}/repositories/sync", headers=headers
            ).status_code
            == 200
        )

    assert len(client.get(f"/api/v1/workspaces/{workspace}/repositories").json()) == 1


@pytest.mark.parametrize("role", [WorkspaceRole.reviewer, WorkspaceRole.viewer])
def test_read_only_roles_cannot_sync_or_change_repository_tracking(
    github_app_client: tuple[TestClient, FakeGitHubAppProvider], role: WorkspaceRole
) -> None:
    client, _ = github_app_client
    workspace = workspace_id(client)
    headers = mutation_headers(client)
    client.post(
        f"/api/v1/workspaces/{workspace}/github/installation",
        json={"installationId": 42},
        headers=headers,
    )
    repo_id = client.post(
        f"/api/v1/workspaces/{workspace}/repositories/sync", headers=headers
    ).json()[0]["id"]
    session = client.get("/api/v1/auth/browser/session").json()
    user_id = UUID(cast(str, session["user"]["id"]))
    app = cast(FastAPI, client.app)
    sessions = cast(async_sessionmaker[AsyncSession], app.state.testing_session)

    async def set_read_only_role() -> None:
        async with sessions.begin() as db:
            await db.execute(
                update(WorkspaceMembership)
                .where(
                    WorkspaceMembership.workspace_id == UUID(workspace),
                    WorkspaceMembership.user_id == user_id,
                )
                .values(role=role)
            )

    assert client.portal is not None
    client.portal.call(set_read_only_role)
    sync = client.post(f"/api/v1/workspaces/{workspace}/repositories/sync", headers=headers)
    tracking = client.patch(
        f"/api/v1/workspaces/{workspace}/repositories/{repo_id}/tracking",
        json={"isTracked": True},
        headers=headers,
    )

    assert sync.status_code == tracking.status_code == 403
    assert sync.json()["code"] == tracking.json()["code"] == "workspace_write_forbidden"


def test_installation_status_requires_browser_membership_and_exposes_no_credentials(
    github_app_client: tuple[TestClient, FakeGitHubAppProvider],
) -> None:
    client, _ = github_app_client
    workspace = workspace_id(client)

    disconnected = client.get(f"/api/v1/workspaces/{workspace}/github/installation")
    attached = client.post(
        f"/api/v1/workspaces/{workspace}/github/installation",
        json={"installationId": 42},
        headers=mutation_headers(client),
    )
    connected = client.get(f"/api/v1/workspaces/{workspace}/github/installation")
    missing_membership = client.get(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000001/github/installation"
    )
    client.cookies.clear()
    unauthenticated = client.get(f"/api/v1/workspaces/{workspace}/github/installation")

    assert disconnected.status_code == 200
    assert disconnected.json() == {
        "connected": False,
        "status": None,
        "accountLogin": None,
        "installationId": None,
    }
    assert attached.status_code == 200
    assert connected.json() == {
        "connected": True,
        "status": "active",
        "accountLogin": "acme",
        "installationId": 42,
    }
    assert "credential" not in str(connected.json()).lower()
    assert missing_membership.status_code == 403
    assert unauthenticated.status_code == 401


@pytest.mark.asyncio
async def test_http_github_app_provider_brokers_tokens_server_side() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    authorizations: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        authorization = request.headers["Authorization"]
        authorizations.append(authorization)
        if request.url.path == "/app/installations/42":
            assert authorization.startswith("Bearer ey")
            return httpx.Response(200, json={"id": 42, "account": {"login": "acme"}})
        if request.url.path == "/app/installations/42/access_tokens":
            assert authorization.startswith("Bearer ey")
            return httpx.Response(201, json={"token": "installation-secret"})
        if request.url.path == "/installation/repositories":
            assert authorization == "Bearer installation-secret"
            return httpx.Response(
                200,
                json={
                    "repositories": [
                        {
                            "id": 100,
                            "name": "api",
                            "full_name": "acme/api",
                            "private": True,
                            "default_branch": "main",
                            "language": "Python",
                            "description": "API",
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    provider = HttpGitHubAppProvider(
        app_id=1,
        private_key=pem,
        http_transport=httpx.MockTransport(transport),
    )

    installation = await provider.verify_installation(42)
    repositories = await provider.list_repositories(42)

    assert installation.account_login == "acme"
    assert repositories[0].full_name == "acme/api"
    assert "installation-secret" not in repr(repositories)
    assert len(authorizations) == 3


@pytest.mark.asyncio
async def test_github_app_providers_reject_unknown_or_malformed_installations() -> None:
    fake = FakeGitHubAppProvider(
        installation=GitHubInstallation(id=42, account_login="acme"), repositories=[]
    )
    with pytest.raises(GitHubAppError, match="not found"):
        await fake.verify_installation(99)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    provider = HttpGitHubAppProvider(
        app_id=1,
        private_key=pem,
        http_transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"id": 42, "account": {}})
        ),
    )

    with pytest.raises(GitHubAppError, match="invalid installation"):
        await provider.verify_installation(42)
