from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.modules.identity.models.oauth import AuthSession
from app.modules.identity.models.users import User
from app.modules.llm.models import generation_runs
from app.modules.llm.policies.llm_route_policy import (
    LLMModelRegistration,
    LLMRoutePlan,
    LLMRoutePolicy,
    LLMRouteStep,
    ModelPricing,
)
from app.modules.llm.providers.fake.fake_llm_provider import FakeLLMProvider
from app.modules.llm.providers.generation_result import (
    LLMGenerationResult,
    LLMGenerationTelemetry,
    LLMUsage,
)
from app.modules.llm.providers.provider_registry import LLMProviderRegistry
from app.modules.llm.services.llm_execution_service import LLMExecutionService
from app.modules.products.models import Product, product_repositories
from app.modules.products.repositories.product_repository import get_product_for_workspace
from app.modules.products.schemas.product_schema import (
    ProductContextGeneratedOutput,
    ProductContextGenerationRequest,
)
from app.modules.products.services.product_generation_service import (
    _metadata,
    _response,
    _saved_output,
    generate_product_context,
)
from app.modules.repos.models import repos
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import Workspace, WorkspaceMembership


def product_llm_service(
    sessions: async_sessionmaker[AsyncSession], output: dict[str, object]
) -> tuple[LLMExecutionService, FakeLLMProvider]:
    provider = FakeLLMProvider(
        LLMGenerationResult(
            output=output,
            telemetry=LLMGenerationTelemetry(
                provider="fake",
                model="fake-v1",
                latency_ms=7,
                usage=LLMUsage(input_tokens=20, output_tokens=40, total_tokens=60),
                correlation_id="context-correlation",
            ),
        )
    )
    registration = LLMModelRegistration(
        name="fake-product-context",
        provider="fake",
        model="fake-v1",
        capabilities={"structured_output"},
        pricing=ModelPricing(input_per_million=0, output_per_million=0),
        pricing_version="test",
    )
    routes = LLMRoutePolicy(
        {registration.name: registration},
        {
            "product-context": LLMRoutePlan(
                name="product-context",
                steps=(LLMRouteStep(model=registration.name),),
                max_attempts=1,
            )
        },
    )
    return LLMExecutionService(sessions, LLMProviderRegistry({"fake": provider}), routes), provider


@pytest.fixture
def product_client() -> Generator[tuple[TestClient, async_sessionmaker[AsyncSession]], None, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

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
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, sessions
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


async def seed_product(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[User, Workspace, Workspace, Product]:
    async with sessions() as db:
        user = User(email="member@example.com")
        workspace = Workspace(name="Member workspace")
        other_workspace = Workspace(name="Other workspace")
        db.add_all([user, workspace, other_workspace])
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, is_active=True))
        product = Product(
            workspace_id=workspace.id,
            user_id=user.id,
            name="Shiptawk",
            description="An AI marketing operator.",
            blocked_terms=["secret"],
            blocked_topics=["private roadmap"],
            safe_public_boundaries=["public releases"],
            proof_points=["Evidence linked"],
            customer_use_cases=["Release marketing"],
            content_goal="awareness",
        )
        db.add(product)
        await db.commit()
        return user, workspace, other_workspace, product


async def seed_product_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[User, Workspace, Product, Product]:
    async with sessions() as db:
        user = User(email="workspace-member@example.com")
        workspace = Workspace(name="Product workspace")
        other_workspace = Workspace(name="Hidden workspace")
        db.add_all([user, workspace, other_workspace])
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, is_active=True))
        created_at = datetime.now(UTC)
        first = Product(
            workspace_id=workspace.id, user_id=user.id, name="First", created_at=created_at
        )
        second = Product(
            workspace_id=workspace.id,
            user_id=user.id,
            name="Second",
            created_at=created_at + timedelta(seconds=1),
        )
        hidden = Product(workspace_id=other_workspace.id, user_id=user.id, name="Hidden")
        db.add_all([first, second, hidden])
        await db.flush()
        repository_id = uuid4()
        await db.execute(
            insert(repos).values(
                id=repository_id,
                workspace_id=workspace.id,
                user_id=user.id,
                repo_name="shiptawk",
                repo_full_name="example/shiptawk",
            )
        )
        await db.execute(
            insert(product_repositories).values(
                id=uuid4(),
                workspace_id=workspace.id,
                product_id=first.id,
                repo_id=repository_id,
                repo_role="backend",
                is_primary_repo=True,
            )
        )
        await db.commit()
        return user, workspace, first, second


def test_product_endpoint_requires_active_membership_and_excludes_persistence_fields(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_product, sessions)

    response = client.get(
        f"/v1/workspaces/{workspace.id}/products/{product.id}",
        headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(product.id),
        "workspaceId": str(workspace.id),
        "name": "Shiptawk",
        "description": "An AI marketing operator.",
        "targetAudience": None,
        "messagingAngle": None,
        "toneOverride": None,
        "blockedTerms": ["secret"],
        "blockedTopics": ["private roadmap"],
        "safePublicBoundaries": ["public releases"],
        "websiteUrl": None,
        "primaryCustomerPain": None,
        "desiredOutcome": None,
        "positioningStatement": None,
        "proofPoints": ["Evidence linked"],
        "customerUseCases": ["Release marketing"],
        "contentGoal": "awareness",
        "ctaPreference": None,
        "founderStoryAngle": None,
        "createdAt": product.created_at.isoformat().replace("+00:00", "Z"),
        "updatedAt": product.updated_at.isoformat().replace("+00:00", "Z"),
    }
    assert "userId" not in response.json()
    assert "createdBy" not in response.json()
    assert "deletedAt" not in response.json()


def test_product_workspace_endpoint_returns_ordered_tenant_safe_aggregate(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, first, second = client.portal.call(seed_product_workspace, sessions)

    response = client.get(
        f"/v1/workspaces/{workspace.id}/products",
        headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [product["id"] for product in body["products"]] == [str(first.id), str(second.id)]
    assert [product["name"] for product in body["products"]] == ["First", "Second"]
    assert len(body["productRepositories"]) == 1
    mapping = body["productRepositories"][0]
    assert mapping["workspaceId"] == str(workspace.id)
    assert mapping["productId"] == str(first.id)
    assert mapping["repoRole"] == "backend"
    assert mapping["isPrimaryRepo"] is True
    assert "userId" not in mapping


def test_product_workspace_endpoint_hides_inactive_and_unknown_workspaces(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_product_workspace, sessions)

    async def deactivate_membership() -> None:
        async with sessions() as db:
            membership = await db.get(
                WorkspaceMembership, {"workspace_id": workspace.id, "user_id": user.id}
            )
            assert membership is not None
            membership.is_active = False
            await db.commit()

    client.portal.call(deactivate_membership)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    inactive = client.get(f"/v1/workspaces/{workspace.id}/products", headers=headers)
    unknown = client.get(f"/v1/workspaces/{uuid4()}/products", headers=headers)

    assert inactive.status_code == unknown.status_code == 404
    for response in (inactive, unknown):
        assert response.json()["detail"] == "Product workspace not found."
        assert response.json()["code"] == "product_workspace_not_found"


def test_product_endpoint_accepts_browser_cookie_session(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_product, sessions)

    async def add_session() -> AuthSession:
        async with sessions() as db:
            session = AuthSession(
                user_id=user.id,
                current_workspace_id=workspace.id,
                refresh_token_hash="cookie-product-session",
                expires_at=datetime.now(UTC) + timedelta(days=1),
                family_id=uuid4(),
            )
            db.add(session)
            await db.commit()
            return session

    session = client.portal.call(add_session)
    client.cookies.set(
        "access_token", create_access_token(str(user.id), session_id=str(session.id))
    )

    response = client.get(f"/v1/workspaces/{workspace.id}/products/{product.id}")

    assert response.status_code == 200


def test_invalid_bearer_never_falls_back_to_valid_browser_cookie(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_product, sessions)
    client.cookies.set("access_token", create_access_token(str(user.id)))

    response = client.get(
        f"/v1/workspaces/{workspace.id}/products/{product.id}",
        headers={"Authorization": "Bearer invalid"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


@pytest.mark.parametrize("resource", ["workspace", "product"])
def test_product_endpoint_returns_same_404_for_cross_workspace_and_missing_resource(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]], resource: str
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, _, other_workspace, product = client.portal.call(seed_product, sessions)
    workspace_id = other_workspace.id if resource == "workspace" else uuid4()
    product_id = product.id if resource == "workspace" else uuid4()

    response = client.get(
        f"/v1/workspaces/{workspace_id}/products/{product_id}",
        headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Product not found."
    assert response.json()["code"] == "product_not_found"


async def test_repository_requires_matching_workspace_and_active_membership() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    user, workspace, other_workspace, product = await seed_product(sessions)

    async with sessions() as db:
        assert await get_product_for_workspace(db, workspace.id, product.id, user.id) is not None
        assert await get_product_for_workspace(db, other_workspace.id, product.id, user.id) is None
        membership = await db.get(
            WorkspaceMembership, {"workspace_id": workspace.id, "user_id": user.id}
        )
        assert membership is not None
        membership.is_active = False
        membership.updated_at = datetime.now(UTC)
        await db.commit()
        assert await get_product_for_workspace(db, workspace.id, product.id, user.id) is None

    await engine.dispose()


async def test_product_workspace_column_is_not_optional() -> None:
    assert Product.workspace_id.property.columns[0].nullable is False


async def seed_product_commands(
    sessions: async_sessionmaker[AsyncSession], role: WorkspaceRole = WorkspaceRole.editor
) -> tuple[User, Workspace, Workspace, Product, Product, UUID, UUID, UUID]:
    async with sessions() as db:
        user = User(email=f"{role.value}-{uuid4()}@example.com")
        workspace = Workspace(name="Writable workspace")
        other_workspace = Workspace(name="Other command workspace")
        db.add_all([user, workspace, other_workspace])
        await db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=user.id, role=role, is_active=True
            )
        )
        first = Product(workspace_id=workspace.id, user_id=user.id, name="First")
        second = Product(workspace_id=workspace.id, user_id=user.id, name="Second")
        db.add_all([first, second])
        await db.flush()
        primary_repo_id = uuid4()
        moving_repo_id = uuid4()
        untracked_repo_id = uuid4()
        await db.execute(
            insert(repos),
            [
                {
                    "id": primary_repo_id,
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "repo_name": "primary",
                    "repo_full_name": "example/primary",
                    "is_tracked": True,
                },
                {
                    "id": moving_repo_id,
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "repo_name": "moving",
                    "repo_full_name": "example/moving",
                    "is_tracked": True,
                },
                {
                    "id": untracked_repo_id,
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "repo_name": "untracked",
                    "repo_full_name": "example/untracked",
                    "is_tracked": False,
                },
            ],
        )
        await db.execute(
            insert(product_repositories),
            [
                {
                    "id": uuid4(),
                    "workspace_id": workspace.id,
                    "product_id": first.id,
                    "repo_id": primary_repo_id,
                    "repo_role": "backend",
                    "is_primary_repo": True,
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace.id,
                    "product_id": first.id,
                    "repo_id": moving_repo_id,
                    "repo_role": "frontend",
                    "is_primary_repo": False,
                    "role_description_override": "Old product override",
                },
            ],
        )
        await db.commit()
        return (
            user,
            workspace,
            other_workspace,
            first,
            second,
            primary_repo_id,
            moving_repo_id,
            untracked_repo_id,
        )


def product_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "New product",
        "description": "A useful product.",
        "websiteUrl": "https://example.com/product",
        "targetAudience": "Founders",
        "messagingAngle": "Ship with evidence",
        "toneOverride": "technical",
        "blockedTerms": ["secret"],
        "blockedTopics": ["roadmap"],
        "safePublicBoundaries": ["released features"],
        "primaryCustomerPain": "Marketing takes too long",
        "desiredOutcome": "Consistent publishing",
        "positioningStatement": "Evidence-backed marketing",
        "proofPoints": ["Linked source"],
        "customerUseCases": ["Release announcements"],
        "contentGoal": "awareness",
        "ctaPreference": "Join the waitlist",
        "founderStoryAngle": "Built from experience",
    }
    payload.update(changes)
    return payload


@pytest.mark.parametrize("role", [WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor])
def test_authorized_workspace_roles_can_create_products(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]], role: WorkspaceRole
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, *_ = client.portal.call(seed_product_commands, sessions, role)

    response = client.post(
        f"/v1/workspaces/{workspace.id}/products",
        headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
        json=product_payload(),
    )

    assert response.status_code == 201
    assert response.json()["workspaceId"] == str(workspace.id)
    assert response.json()["name"] == "New product"
    assert response.json()["websiteUrl"] == "https://example.com/product"
    assert "userId" not in response.json()


def test_product_creation_adds_https_to_bare_website_domain(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, *_ = client.portal.call(seed_product_commands, sessions, WorkspaceRole.owner)

    response = client.post(
        f"/v1/workspaces/{workspace.id}/products",
        headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
        json=product_payload(websiteUrl="example.com/product"),
    )

    assert response.status_code == 201
    assert response.json()["websiteUrl"] == "https://example.com/product"


@pytest.mark.parametrize("role", [WorkspaceRole.reviewer, WorkspaceRole.viewer])
def test_read_only_workspace_roles_cannot_create_products(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]], role: WorkspaceRole
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, *_ = client.portal.call(seed_product_commands, sessions, role)

    response = client.post(
        f"/v1/workspaces/{workspace.id}/products",
        headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
        json=product_payload(),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "product_write_forbidden"


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"description": "x" * 1201},
        {"blockedTerms": ["x" * 121]},
        {"proofPoints": ["x" * 181]},
        {"websiteUrl": "ftp://example.com/file"},
        {"websiteUrl": "http://127.0.0.1/internal"},
        {"websiteUrl": "https://localhost/internal"},
    ],
)
def test_product_write_preserves_frontend_validation_and_public_website_rules(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]], changes: dict[str, object]
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, *_ = client.portal.call(seed_product_commands, sessions)

    response = client.post(
        f"/v1/workspaces/{workspace.id}/products",
        headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
        json=product_payload(**changes),
    )

    assert response.status_code == 422


def test_update_and_delete_product_are_tenant_authorized_and_transactional(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, other_workspace, first, _, primary_repo_id, *_ = client.portal.call(
        seed_product_commands, sessions
    )
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    async def add_product_history() -> UUID:
        async with sessions() as db:
            run_id = uuid4()
            await db.execute(
                insert(generation_runs).values(
                    id=run_id,
                    workspace_id=workspace.id,
                    user_id=user.id,
                    repo_id=primary_repo_id,
                    product_id=first.id,
                    trigger="event_pipeline",
                    context_version=1,
                    context_snapshot={},
                    provider="fake",
                    model="fake",
                    system_prompt="sanitized",
                    user_prompt="sanitized",
                    status="completed",
                )
            )
            await db.commit()
            return run_id

    run_id = client.portal.call(add_product_history)

    hidden = client.put(
        f"/v1/workspaces/{other_workspace.id}/products/{first.id}",
        headers=headers,
        json=product_payload(name="Hidden update"),
    )
    updated = client.put(
        f"/v1/workspaces/{workspace.id}/products/{first.id}",
        headers=headers,
        json=product_payload(name="Updated", websiteUrl=""),
    )
    deleted = client.delete(f"/v1/workspaces/{workspace.id}/products/{first.id}", headers=headers)

    assert hidden.status_code == 404
    assert hidden.json()["code"] == "product_not_found"
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated"
    assert updated.json()["websiteUrl"] is None
    assert deleted.status_code == 204
    assert (
        client.get(
            f"/v1/workspaces/{workspace.id}/products/{first.id}", headers=headers
        ).status_code
        == 404
    )

    async def historical_product_id() -> UUID | None:
        async with sessions() as db:
            return await db.scalar(
                select(generation_runs.c.product_id).where(generation_runs.c.id == run_id)
            )

    assert client.portal.call(historical_product_id) is None


def test_repository_assignment_enforces_tracking_uniqueness_primary_and_move_rules(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, _, _, second, primary_repo_id, moving_repo_id, untracked_repo_id = (
        client.portal.call(seed_product_commands, sessions)
    )
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    untracked = client.put(
        f"/v1/workspaces/{workspace.id}/products/{second.id}/repositories/{untracked_repo_id}",
        headers=headers,
        json={"repoRole": "infra", "isPrimaryRepo": False},
    )
    moved = client.put(
        f"/v1/workspaces/{workspace.id}/products/{second.id}/repositories/{moving_repo_id}",
        headers=headers,
        json={"repoRole": "worker", "isPrimaryRepo": True},
    )

    assert untracked.status_code == 409
    assert untracked.json()["code"] == "repository_not_tracked"
    assert moved.status_code == 200
    assert moved.json()["productId"] == str(second.id)
    assert moved.json()["repoRole"] == "worker"
    assert moved.json()["isPrimaryRepo"] is True
    assert moved.json()["roleDescriptionOverride"] is None

    async def mapping_state() -> list[dict[str, object]]:
        async with sessions() as db:
            rows = (
                await db.execute(
                    select(product_repositories).where(
                        product_repositories.c.repo_id.in_([primary_repo_id, moving_repo_id])
                    )
                )
            ).mappings()
            return [dict(row) for row in rows]

    mappings = client.portal.call(mapping_state)
    assert len([row for row in mappings if row["repo_id"] == moving_repo_id]) == 1
    moved_mapping = next(row for row in mappings if row["repo_id"] == moving_repo_id)
    assert moved_mapping["product_id"] == second.id


def test_repository_primary_switch_removal_and_role_description_commands(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, _, first, _, primary_repo_id, moving_repo_id, _ = client.portal.call(
        seed_product_commands, sessions
    )
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    primary = client.put(
        f"/v1/workspaces/{workspace.id}/products/{first.id}/repositories/{moving_repo_id}",
        headers=headers,
        json={"repoRole": "frontend", "isPrimaryRepo": True},
    )
    described = client.patch(
        f"/v1/workspaces/{workspace.id}/products/{first.id}/repositories/{moving_repo_id}/role-description",
        headers=headers,
        json={"roleDescriptionOverride": "Owns the public application."},
    )
    removed = client.delete(
        f"/v1/workspaces/{workspace.id}/products/{first.id}/repositories/{moving_repo_id}",
        headers=headers,
    )

    assert primary.status_code == 200
    assert described.status_code == 200
    assert described.json()["roleDescriptionOverride"] == "Owns the public application."
    assert removed.status_code == 204

    async def remaining_state() -> tuple[bool, int]:
        async with sessions() as db:
            old_primary = await db.scalar(
                select(product_repositories.c.is_primary_repo).where(
                    product_repositories.c.repo_id == primary_repo_id
                )
            )
            moving_count = len(
                list(
                    await db.scalars(
                        select(product_repositories.c.id).where(
                            product_repositories.c.repo_id == moving_repo_id
                        )
                    )
                )
            )
            return bool(old_primary), moving_count

    assert client.portal.call(remaining_state) == (False, 0)


def test_repository_commands_hide_cross_workspace_resources(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, _, other_workspace, first, _, _, moving_repo_id, _ = client.portal.call(
        seed_product_commands, sessions
    )

    response = client.put(
        f"/v1/workspaces/{other_workspace.id}/products/{first.id}/repositories/{moving_repo_id}",
        headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
        json={"repoRole": "backend", "isPrimaryRepo": False},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "product_repository_not_found"


def test_product_context_generation_is_tenant_scoped_idempotent_and_persisted(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, other_workspace, product = client.portal.call(seed_product, sessions)
    llm, provider = product_llm_service(
        sessions,
        {
            "name": "Shiptawk",
            "description": "An evidence-backed marketing operator for startup teams.",
            "targetAudience": "Technical startup teams",
            "mainMarket": "Startup marketing software",
            "mainValueProposition": "Turn product evidence into focused marketing action.",
            "messagingAngle": "Turn verified product progress into marketing action.",
            "toneOverride": "technical",
            "safePublicBoundaries": ["Public releases only"],
            "primaryCustomerPain": "Marketing work is fragmented",
            "desiredOutcome": "A consistent evidence-backed operating loop",
            "positioningStatement": "Operate startup marketing with evidence",
            "proofPoints": ["Every claim links to evidence"],
            "customerUseCases": ["Release marketing"],
            "contentGoal": "awareness",
            "ctaPreference": "Review the weekly plan",
            "founderStoryAngle": "Built for technical founders",
            "confidence": 86,
        },
    )
    app = cast(FastAPI, client.app)
    app.state.llm_execution_service = llm
    payload = {
        "websiteUrl": "https://example.com",
        "force": True,
        "idempotencyKey": "product-context:test-1",
    }
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    first = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/context-generation",
        headers=headers,
        json=payload,
    )
    replay = client.post(
        f"/v1/workspaces/{workspace.id}/products/{product.id}/context-generation",
        headers=headers,
        json=payload,
    )
    hidden = client.post(
        f"/v1/workspaces/{other_workspace.id}/products/{product.id}/context-generation",
        headers=headers,
        json={**payload, "idempotencyKey": "product-context:hidden"},
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["draft"]["description"].startswith("An evidence-backed")
    assert first.json()["draft"]["mainMarket"] == "Startup marketing software"
    assert first.json()["draft"]["mainValueProposition"].startswith("Turn product evidence")
    assert first.json()["draft"]["blockedTerms"] == "secret"
    assert first.json()["confidence"] == 86
    assert len(provider.requests) == 1
    assert hidden.status_code == 404
    assert hidden.json()["code"] == "product_not_found"
    prompt = " ".join(message.content for message in provider.requests[0].messages)
    assert "secret" not in prompt
    assert "private roadmap" not in prompt
    assert provider.requests[0].output_json_schema == (
        ProductContextGeneratedOutput.model_json_schema(by_alias=True)
    )

    async def persisted_run() -> dict[str, object]:
        async with sessions() as db:
            row = (
                (
                    await db.execute(
                        select(generation_runs).where(
                            generation_runs.c.workspace_id == workspace.id,
                            generation_runs.c.trigger == "product_context",
                        )
                    )
                )
                .mappings()
                .one()
            )
            return dict(row)

    run = client.portal.call(persisted_run)
    assert run["status"] == "completed"
    assert run["repo_id"] is None
    assert run["provider"] == "fake"
    assert run["model"] == "fake-v1"
    assert run["system_prompt"] == "[not persisted]"
    provider_parameters = cast(dict[str, object], run["provider_parameters"])
    assert provider_parameters["promptVersion"] == "product-context.v2"


def test_saved_product_context_uses_fallbacks_and_marks_low_confidence_for_review() -> None:
    product = Product(
        workspace_id=uuid4(),
        user_id=uuid4(),
        name="Shiptawk",
        blocked_terms=["secret"],
        blocked_topics=["roadmap"],
        safe_public_boundaries=[],
        proof_points=[],
        customer_use_cases=[],
        content_goal="",
    )

    saved = _saved_output(product)
    response = _response(
        product,
        saved.model_copy(update={"confidence": 64, "safe_public_boundaries": ["Public releases"]}),
        "https://example.com",
        source_note="Using saved context.",
    )

    assert saved.description == "Shiptawk product context."
    assert saved.target_audience == "Product users"
    assert saved.main_market == "Software"
    assert response.needs_review is True
    assert response.draft.blocked_terms == "secret"
    assert response.draft.blocked_topics == "roadmap"
    assert response.draft.safe_public_boundaries == "Public releases"


def test_generation_metadata_keeps_telemetry_without_prompt_content() -> None:
    execution_id = uuid4()
    telemetry = LLMGenerationTelemetry(
        provider="deterministic",
        model="fake-v1",
        latency_ms=12,
        usage=LLMUsage(input_tokens=20, output_tokens=30, total_tokens=50),
        estimated_cost=0.01,
        correlation_id="product-context:correlation",
    )

    metadata = _metadata(telemetry, execution_id)

    assert metadata == {
        "requestParameters": {"temperature": 0},
        "promptVersion": "product-context.v2",
        "correlationId": "product-context:correlation",
        "llmExecutionId": str(execution_id),
        "usage": {
            "input_tokens": 20,
            "output_tokens": 30,
            "total_tokens": 50,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        },
        "costEstimateUsd": 0.01,
        "failureCategory": None,
    }


async def test_product_context_service_persists_and_reuses_a_deterministic_generation(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_product, sessions)
    llm, provider = product_llm_service(
        sessions,
        {
            "name": "Shiptawk",
            "description": "Evidence-backed marketing for product teams.",
            "targetAudience": "Product teams",
            "mainMarket": "Product marketing software",
            "mainValueProposition": "Create consistent marketing from product evidence.",
            "messagingAngle": "Turn releases into reviewed marketing.",
            "toneOverride": "technical",
            "safePublicBoundaries": ["Public releases"],
            "primaryCustomerPain": "Marketing is fragmented",
            "desiredOutcome": "Consistent publishing",
            "positioningStatement": "Evidence-backed marketing operator",
            "proofPoints": ["Claims link to evidence"],
            "customerUseCases": ["Release marketing"],
            "contentGoal": "awareness",
            "ctaPreference": "Review the plan",
            "founderStoryAngle": "Built by a technical founder",
            "confidence": 80,
        },
    )
    payload = ProductContextGenerationRequest(
        website_url="https://example.com", force=True, idempotency_key="service-test"
    )

    async with sessions() as db:
        first = await generate_product_context(db, workspace.id, product.id, user.id, payload, llm)
        second = await generate_product_context(db, workspace.id, product.id, user.id, payload, llm)

    assert first == second
    assert first.draft.description == "Evidence-backed marketing for product teams."
    assert first.needs_review is False
    assert len(provider.requests) == 1
