from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_browser_session, require_cookie_auth_csrf
from app.core.exception_handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.modules.identity.api.router import router as user_router
from app.modules.identity.events.onboarding import OnboardingGenerationRequest
from app.modules.identity.models.oauth import AuthSession, OAuthIdentity
from app.modules.identity.models.users import User
from app.modules.identity.providers.onboarding import DurableOnboardingGenerationTrigger
from app.modules.identity.schemas.users import UserOnboardingStatusResponse
from app.modules.identity.services.users import (
    complete_onboarding,
    get_active_user_by_id,
    get_generation_settings,
    get_onboarding_status,
    get_session_workspace_id,
    get_user_profile,
    record_dashboard_view,
)
from app.modules.integrations.models import IntegrationConnection
from app.modules.products.models import Product, product_repositories
from app.modules.repos.models import repos
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import Workspace, WorkspaceMembership
from app.shared.exceptions import ConflictError, UnauthorizedError
from app.workflows.publisher import MetadataEvent


class FakeOnboardingGenerationTrigger:
    def __init__(self) -> None:
        self.requests: list[OnboardingGenerationRequest] = []

    async def trigger(self, request: OnboardingGenerationRequest) -> None:
        self.requests.append(request)


@pytest.fixture
def user_client() -> Generator[tuple[TestClient, dict[str, UUID]], None, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    trigger = FakeOnboardingGenerationTrigger()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(user_router, prefix="/api/v1")
    app.state.onboarding_generation_trigger = trigger

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    async def seed() -> dict[str, UUID]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions.begin() as db:
            user = User(email="octocat@example.com", github_id="123", github_username="legacy")
            other = User(email="other@example.com")
            workspace = Workspace(name="Octo workspace")
            other_workspace = Workspace(name="Other workspace")
            db.add_all([user, other, workspace, other_workspace])
            await db.flush()
            db.add_all(
                [
                    WorkspaceMembership(
                        workspace_id=workspace.id,
                        user_id=user.id,
                        role=WorkspaceRole.owner,
                    ),
                    WorkspaceMembership(
                        workspace_id=other_workspace.id,
                        user_id=other.id,
                        role=WorkspaceRole.owner,
                    ),
                    OAuthIdentity(
                        user_id=user.id,
                        provider="github",
                        provider_subject="123",
                        email="octocat@example.com",
                        username="octocat",
                        display_name="Octo Cat",
                        avatar_url="https://avatars.example/octocat.png",
                        email_verified=True,
                        last_login_at=datetime.now(UTC),
                    ),
                ]
            )
            session_id = uuid4()
            db.add(
                AuthSession(
                    id=session_id,
                    user_id=user.id,
                    current_workspace_id=workspace.id,
                    refresh_token_hash=uuid4().hex,
                    expires_at=datetime(2100, 1, 1, tzinfo=UTC),
                    family_id=uuid4(),
                )
            )
        return {
            "user_id": user.id,
            "workspace_id": workspace.id,
            "other_workspace_id": other_workspace.id,
            "session_id": session_id,
        }

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        assert client.portal is not None
        ids = client.portal.call(seed)

        async def browser_session() -> tuple[User, UUID]:
            async with sessions() as db:
                user = await db.get(User, ids["user_id"])
                assert user is not None
                return user, ids["session_id"]

        async def mutation_session() -> tuple[User, UUID]:
            return await browser_session()

        app.dependency_overrides[get_browser_session] = browser_session
        app.dependency_overrides[require_cookie_auth_csrf] = mutation_session
        app.state.testing_sessions = sessions
        yield client, ids
        client.portal.call(engine.dispose)


def test_profile_exposes_only_safe_current_user_identity(
    user_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    client, ids = user_client

    response = client.get(
        "/api/v1/user/profile",
        headers={"Authorization": f"Bearer {create_access_token(str(ids['user_id']))}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(ids["user_id"]),
        "email": "octocat@example.com",
        "githubUsername": "octocat",
        "githubDisplayName": "Octo Cat",
        "githubAvatarUrl": "https://avatars.example/octocat.png",
        "onboardingCompleted": False,
    }
    serialized = response.text.lower()
    assert "provider_subject" not in serialized
    assert "token" not in serialized
    assert "credential" not in serialized


def test_onboarding_status_is_scoped_to_the_current_workspace(
    user_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    client, ids = user_client
    app = cast(FastAPI, client.app)
    sessions = cast(async_sessionmaker[AsyncSession], app.state.testing_sessions)

    async def seed_other_workspace_only() -> None:
        async with sessions.begin() as db:
            await db.execute(
                insert(repos).values(
                    id=uuid4(),
                    workspace_id=ids["other_workspace_id"],
                    user_id=ids["user_id"],
                    repo_name="other",
                    repo_full_name="elsewhere/other",
                    is_tracked=True,
                )
            )

    assert client.portal is not None
    client.portal.call(seed_other_workspace_only)

    response = client.get("/api/v1/user/onboarding")

    assert response.status_code == 200
    assert response.json() == {
        "completed": False,
        "githubConnected": False,
        "reposSynced": False,
        "hasTrackedRepos": False,
        "trackedReposAssigned": False,
        "usedProductsHaveContext": False,
        "twitterConnected": False,
        "nextStep": "connect_github",
    }


def test_completion_validates_workspace_setup_and_triggers_generation_once(
    user_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    client, ids = user_client
    app = cast(FastAPI, client.app)
    sessions = cast(async_sessionmaker[AsyncSession], app.state.testing_sessions)
    trigger = cast(FakeOnboardingGenerationTrigger, app.state.onboarding_generation_trigger)

    incomplete = client.post("/api/v1/user/onboarding/complete")
    assert incomplete.status_code == 409
    assert incomplete.json()["code"] == "onboarding_requirements_not_met"

    async def seed_complete_setup() -> None:
        async with sessions.begin() as db:
            repo_id = uuid4()
            product = Product(
                workspace_id=ids["workspace_id"],
                user_id=ids["user_id"],
                name="Shiptawk",
            )
            db.add(product)
            await db.flush()
            db.add(
                IntegrationConnection(
                    workspace_id=ids["workspace_id"],
                    provider="github_app",
                    external_account_id="99",
                    credentials_ciphertext="server-managed-github-app",
                    credential_key_version="none",
                    idempotency_key=f"github-app:{uuid4()}",
                )
            )
            await db.execute(
                insert(repos).values(
                    id=repo_id,
                    workspace_id=ids["workspace_id"],
                    user_id=ids["user_id"],
                    repo_name="app",
                    repo_full_name="octo/app",
                    is_tracked=True,
                )
            )
            await db.execute(
                insert(product_repositories).values(
                    id=uuid4(),
                    workspace_id=ids["workspace_id"],
                    product_id=product.id,
                    repo_id=repo_id,
                )
            )

    assert client.portal is not None
    client.portal.call(seed_complete_setup)

    first = client.post("/api/v1/user/onboarding/complete")
    second = client.post("/api/v1/user/onboarding/complete")

    assert first.status_code == second.status_code == 200
    assert first.json()["completed"] is True
    assert len(trigger.requests) == 1
    assert trigger.requests[0] == OnboardingGenerationRequest(
        user_id=ids["user_id"],
        workspace_id=ids["workspace_id"],
        event_id=f"onboarding:{ids['user_id']}:{ids['workspace_id']}",
    )

    async def status_in_other_workspace() -> UserOnboardingStatusResponse:
        async with sessions.begin() as db:
            db.add(
                WorkspaceMembership(
                    workspace_id=ids["other_workspace_id"],
                    user_id=ids["user_id"],
                    role=WorkspaceRole.editor,
                )
            )
        async with sessions() as db:
            current_user = await db.get(User, ids["user_id"])
            assert current_user is not None
            return await get_onboarding_status(
                db, user=current_user, workspace_id=ids["other_workspace_id"]
            )

    other_status = client.portal.call(status_in_other_workspace)
    assert other_status.completed is False
    assert other_status.next_step.value == "connect_github"


def test_dashboard_view_updates_only_the_current_user(
    user_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    client, ids = user_client
    app = cast(FastAPI, client.app)
    sessions = cast(async_sessionmaker[AsyncSession], app.state.testing_sessions)

    response = client.post("/api/v1/user/dashboard-view")

    assert response.status_code == 204

    async def viewed_at() -> datetime | None:
        async with sessions() as db:
            return await db.scalar(
                select(User.last_dashboard_viewed_at).where(User.id == ids["user_id"])
            )

    assert client.portal is not None
    assert client.portal.call(viewed_at) is not None


def test_onboarding_status_reports_each_backend_owned_setup_stage(
    user_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    client, ids = user_client
    app = cast(FastAPI, client.app)
    sessions = cast(async_sessionmaker[AsyncSession], app.state.testing_sessions)
    repo_id = uuid4()
    product_id = uuid4()

    async def add_github() -> None:
        async with sessions.begin() as db:
            db.add(
                IntegrationConnection(
                    workspace_id=ids["workspace_id"],
                    provider="github_app",
                    external_account_id="stage-test",
                    credentials_ciphertext="server-managed-github-app",
                    credential_key_version="none",
                    idempotency_key=f"github-app:{uuid4()}",
                )
            )

    async def add_untracked_repo() -> None:
        async with sessions.begin() as db:
            await db.execute(
                insert(repos).values(
                    id=repo_id,
                    workspace_id=ids["workspace_id"],
                    user_id=ids["user_id"],
                    repo_name="stages",
                    repo_full_name="octo/stages",
                    is_tracked=False,
                )
            )

    async def track_repo() -> None:
        async with sessions.begin() as db:
            await db.execute(update(repos).where(repos.c.id == repo_id).values(is_tracked=True))

    async def assign_repo_without_context() -> None:
        async with sessions.begin() as db:
            db.add(
                Product(
                    id=product_id,
                    workspace_id=ids["workspace_id"],
                    user_id=ids["user_id"],
                    name="Stage product",
                )
            )
            await db.execute(
                insert(product_repositories).values(
                    id=uuid4(),
                    workspace_id=ids["workspace_id"],
                    product_id=product_id,
                    repo_id=repo_id,
                )
            )

    async def add_context_and_twitter() -> None:
        async with sessions.begin() as db:
            await db.execute(
                update(Product)
                .where(Product.id == product_id)
                .values(description="A useful product", target_audience="Founders")
            )
            db.add(
                IntegrationConnection(
                    workspace_id=ids["workspace_id"],
                    provider="x",
                    external_account_id="x-stage-test",
                    credentials_ciphertext="encrypted",
                    credential_key_version="v1",
                    idempotency_key=f"x:{uuid4()}",
                )
            )

    assert client.portal is not None
    client.portal.call(add_github)
    assert client.get("/api/v1/user/onboarding").json()["nextStep"] == "sync_repos"
    client.portal.call(add_untracked_repo)
    assert client.get("/api/v1/user/onboarding").json()["nextStep"] == "track_repos"
    client.portal.call(track_repo)
    assert client.get("/api/v1/user/onboarding").json()["nextStep"] == "group_products"
    client.portal.call(assign_repo_without_context)
    assert client.get("/api/v1/user/onboarding").json()["nextStep"] == "product_context"
    client.portal.call(add_context_and_twitter)
    status = client.get("/api/v1/user/onboarding").json()
    assert status["nextStep"] == "complete"
    assert status["usedProductsHaveContext"] is True
    assert status["twitterConnected"] is True


@pytest.mark.asyncio
async def test_durable_onboarding_trigger_sends_only_deterministic_metadata() -> None:
    @dataclass
    class FakePublisher:
        events: list[MetadataEvent] = field(default_factory=list)

        async def publish(self, event: MetadataEvent) -> None:
            self.events.append(event)

    publisher = FakePublisher()
    trigger = DurableOnboardingGenerationTrigger(publisher)
    request = OnboardingGenerationRequest(
        user_id=uuid4(), workspace_id=uuid4(), event_id="onboarding:stable-user"
    )

    await trigger.trigger(request)

    assert publisher.events == [
        MetadataEvent(
            name="user/onboarding.completed",
            event_id="onboarding:stable-user",
            data={
                "userId": str(request.user_id),
                "workspaceId": str(request.workspace_id),
                "schemaVersion": 1,
            },
        )
    ]


async def test_user_service_resolves_session_profile_and_initial_onboarding_state_directly(
    user_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    client, ids = user_client
    app = cast(FastAPI, client.app)
    sessions = cast(async_sessionmaker[AsyncSession], app.state.testing_sessions)

    async with sessions() as db:
        user = await db.get(User, ids["user_id"])
        assert user is not None
        assert await get_active_user_by_id(db, user.id) == user
        with pytest.raises(UnauthorizedError, match="Invalid authentication"):
            await get_active_user_by_id(db, uuid4())
        workspace_id = await get_session_workspace_id(
            db, user_id=user.id, session_id=ids["session_id"]
        )
        profile = await get_user_profile(db, user)
        onboarding = await get_onboarding_status(db, user=user, workspace_id=workspace_id)
        await record_dashboard_view(db, user)
        with pytest.raises(UnauthorizedError, match="Invalid browser session"):
            await get_session_workspace_id(db, user_id=user.id, session_id=uuid4())
        with pytest.raises(ConflictError, match="GitHub"):
            await complete_onboarding(db, user=user, workspace_id=workspace_id, trigger=None)

    invalid_preferences = User(
        email="invalid-preferences@example.com",
        voice_profile={"tone_modes": "not-a-list"},
        safety_settings={"max_drafts_per_week": "many"},
        tone_preference="casual",
        email_notifications_enabled=True,
        achievement_digest_enabled=False,
        achievement_digest_frequency="weekly",
        achievement_digest_prompt_dismissed=False,
        achievement_digest_onboarding_seen=False,
    )
    settings = get_generation_settings(invalid_preferences)

    assert workspace_id == ids["workspace_id"]
    assert profile.github_username == "octocat"
    assert onboarding.next_step.value == "connect_github"
    assert settings.voice_profile.tone_modes == ["concise", "direct"]
    assert settings.safety_settings.max_drafts_per_week == 5
