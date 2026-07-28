from collections.abc import AsyncGenerator, Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.modules.identity.models.users import User
from app.modules.products.models import Product
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import Workspace, WorkspaceMembership


@pytest.fixture
def profile_client() -> Generator[tuple[TestClient, async_sessionmaker[AsyncSession]], None, None]:
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


async def seed_profile_product(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[User, Workspace, Workspace, Product]:
    async with sessions() as db:
        user = User(email="profile-owner@example.com")
        workspace = Workspace(name="Profile workspace")
        other_workspace = Workspace(name="Other workspace")
        db.add_all([user, workspace, other_workspace])
        await db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceRole.owner,
                is_active=True,
            )
        )
        product = Product(workspace_id=workspace.id, user_id=user.id, name="Shiptawk")
        db.add(product)
        await db.commit()
        return user, workspace, other_workspace, product


def complete_payload() -> dict[str, object]:
    return {
        "productName": "Shiptawk",
        "shortDescription": "An evidence-first AI marketing operator.",
        "primaryAudience": "Small software teams",
        "primaryCustomerProblem": "Marketing becomes inconsistent after shipping.",
        "mainValueProposition": "Turn product evidence into approved marketing work.",
        "primaryConversionGoal": "signup",
        "primaryConversionUrl": "https://example.com/signup",
        "mainMarket": "Global B2B SaaS",
        "differentiation": "Every recommendation retains evidence provenance.",
        "importantCapabilities": ["Evidence-backed planning"],
        "quarterlyObjective": "Increase qualified signups",
        "restrictedTopics": [],
        "restrictedClaims": ["Guaranteed growth"],
        "restrictedLanguage": ["Revolutionary"],
        "brandVoiceGuidance": "Clear, direct, and specific.",
    }


def test_product_profile_first_draft_approval_and_version_history(
    profile_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = profile_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_profile_product, sessions)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    path = f"/api/v1/workspaces/{workspace.id}/products/{product.id}/product-profile"

    created = client.put(path, json=complete_payload(), headers=headers)
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "draft"
    assert created.json()["draftRevision"] == 1
    assert created.json()["completenessScore"] == 100

    approved = client.post(path + "/approve", json={"expectedRevision": 1}, headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["version"] == 1

    version = client.get(path + "/versions/1", headers=headers)
    assert version.status_code == 200
    assert version.json()["id"] == approved.json()["id"]

    second_draft = client.put(
        path,
        json=complete_payload() | {"quarterlyObjective": "Increase qualified demos"},
        headers=headers,
    )
    assert second_draft.status_code == 200
    assert second_draft.json()["basedOnProfileId"] == approved.json()["id"]

    stale = client.put(path, json=complete_payload(), headers=headers)
    assert stale.status_code == 409
    assert stale.json()["code"] == "product_profile_revision_conflict"

    updated = client.put(
        path,
        json=complete_payload() | {"expectedRevision": 1},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["draftRevision"] == 2

    second_approval = client.post(path + "/approve", json={"expectedRevision": 2}, headers=headers)
    assert second_approval.status_code == 200
    assert second_approval.json()["version"] == 2

    original = client.get(path + "/versions/1", headers=headers)
    assert original.json()["status"] == "superseded"
    assert original.json()["quarterlyObjective"] == "Increase qualified signups"


def test_product_profile_rejects_private_conversion_url_and_cross_workspace_access(
    profile_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = profile_client
    assert client.portal is not None
    user, _, other_workspace, product = client.portal.call(seed_profile_product, sessions)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    payload = complete_payload() | {"primaryConversionUrl": "http://127.0.0.1/signup"}

    invalid = client.put(
        f"/api/v1/workspaces/{other_workspace.id}/products/{product.id}/product-profile",
        json=complete_payload(),
        headers=headers,
    )
    assert invalid.status_code == 404

    own_path = f"/api/v1/workspaces/{product.workspace_id}/products/{product.id}/product-profile"
    unsafe = client.put(own_path, json=payload, headers=headers)
    assert unsafe.status_code == 422
