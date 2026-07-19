from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.domains.products.models import Product
from app.domains.products.repository import get_product_for_workspace
from app.domains.users.models import User
from app.domains.workspaces.models import Workspace, WorkspaceMembership
from app.main import create_app


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
        user = User(email="member@example.com", password_hash="not-a-real-hash")
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


def test_product_endpoint_requires_active_membership_and_excludes_persistence_fields(
    product_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = product_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_product, sessions)

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/products/{product.id}",
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
        f"/api/v1/workspaces/{workspace_id}/products/{product_id}",
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
