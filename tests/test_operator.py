from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.domains.auth.models import AuthSession
from app.domains.evidence.models import EvidenceItem
from app.domains.operator.models import (
    ApprovalRequest,
    ExecutionRun,
    MarketingPlan,
    MeasurementWindow,
    OperatorRun,
    Opportunity,
    PlanAction,
    VerificationRun,
)
from app.domains.operator.repository import get_plan_with_actions, get_resource, list_resources
from app.domains.operator.router import router as operator_router
from app.domains.operator.schemas import OpportunityCreate
from app.domains.products.models import Product
from app.domains.users.models import User
from app.domains.workspaces.models import Workspace, WorkspaceMembership, WorkspaceRole
from app.main import create_app


@pytest.fixture
def operator_client() -> Generator[tuple[TestClient, async_sessionmaker[AsyncSession]], None, None]:
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
    app.include_router(operator_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, sessions
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


async def seed_operator(
    sessions: async_sessionmaker[AsyncSession], *, role: WorkspaceRole = WorkspaceRole.owner
) -> tuple[User, Workspace, Workspace, Product, EvidenceItem]:
    async with sessions() as db:
        user = User(email=f"operator-{uuid4()}@example.com")
        workspace = Workspace(name="Operator workspace")
        other_workspace = Workspace(name="Hidden workspace")
        db.add_all([user, workspace, other_workspace])
        await db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=user.id, role=role, is_active=True
            )
        )
        product = Product(workspace_id=workspace.id, user_id=user.id, name="Operator product")
        db.add(product)
        await db.flush()
        evidence = EvidenceItem(
            workspace_id=workspace.id,
            product_id=product.id,
            evidence_type="manual_note",
            title="Search pages need clearer titles",
            content="Three approved pages have duplicate titles.",
            public_safe_summary="Duplicate titles were observed.",
            source_type="manual",
            source_reference="audit-1",
            observed_at=datetime.now(UTC),
            confidence="high",
            classification="public",
            status="approved",
            created_by_actor_id=user.id,
        )
        db.add(evidence)
        await db.commit()
        return user, workspace, other_workspace, product, evidence


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def recommendation_payload(evidence_id: UUID, **changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "productId": None,
        "title": "Improve duplicate page titles",
        "evidenceIds": [str(evidence_id)],
        "interpretation": "Duplicate titles weaken page differentiation.",
        "recommendation": "Draft unique titles for the affected pages.",
        "confidence": "high",
        "missingInformation": [],
        "approvalLevel": "review",
        "measurementWindow": "28 days",
        "stopConditions": ["Evidence becomes stale"],
        "impactScore": 80,
        "effortScore": 20,
        "urgencyScore": 60,
        "cooldownDedupKey": "seo:duplicate-titles",
        "cooldownDays": 28,
        "provenance": {"analyzer": "manual-api", "version": "1"},
    }
    payload.update(changes)
    return payload


def test_recommendation_requires_evidence_confidence_and_missing_information() -> None:
    evidence_id = uuid4()
    model = OpportunityCreate.model_validate(recommendation_payload(evidence_id))
    assert model.confidence == "high"
    assert model.missing_information == []

    for missing in ("evidenceIds", "confidence", "missingInformation"):
        payload = recommendation_payload(evidence_id)
        del payload[missing]
        with pytest.raises(ValueError):
            OpportunityCreate.model_validate(payload)

    with pytest.raises(ValueError):
        OpportunityCreate.model_validate(recommendation_payload(evidence_id, evidenceIds=[]))


def test_manual_opportunity_is_tenant_safe_and_cooldown_deduplicated(
    operator_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = operator_client
    assert client.portal is not None
    user, workspace, other_workspace, product, evidence = client.portal.call(
        seed_operator, sessions
    )
    payload = recommendation_payload(evidence.id, productId=str(product.id))

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/opportunities", headers=auth(user), json=payload
    )
    duplicate = client.post(
        f"/api/v1/workspaces/{workspace.id}/opportunities", headers=auth(user), json=payload
    )
    hidden = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/opportunities/{created.json()['id']}",
        headers=auth(user),
    )
    hidden_list = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/opportunities", headers=auth(user)
    )
    listed = client.get(f"/api/v1/workspaces/{workspace.id}/opportunities", headers=auth(user))

    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "opportunity_cooldown_active"
    assert hidden.status_code == 404
    assert hidden_list.status_code == 404
    assert [item["id"] for item in listed.json()["items"]] == [created.json()["id"]]
    fetched = client.get(
        f"/api/v1/workspaces/{workspace.id}/opportunities/{created.json()['id']}",
        headers=auth(user),
    )
    plan = client.post(
        f"/api/v1/workspaces/{workspace.id}/plans",
        headers=auth(user),
        json={
            "title": "Manual weekly plan",
            "opportunityIds": [created.json()["id"]],
            "idempotencyKey": "manual-plan:1",
        },
    )
    assert fetched.status_code == 200
    assert plan.status_code == 201
    assert plan.json()["actions"][0]["opportunityId"] == created.json()["id"]
    for path in (
        f"operator/runs/{uuid4()}",
        f"plans/{uuid4()}",
        f"approval-requests/{uuid4()}",
        f"executions/{uuid4()}",
    ):
        assert (
            client.get(f"/api/v1/workspaces/{workspace.id}/{path}", headers=auth(user)).status_code
            == 404
        )


def test_weekly_run_is_idempotent_and_builds_bounded_provenance_linked_plan(
    operator_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = operator_client
    assert client.portal is not None
    user, workspace, _, product, evidence = client.portal.call(seed_operator, sessions)
    payload = {"productId": str(product.id), "idempotencyKey": "weekly:2026-30"}

    first = client.post(
        f"/api/v1/workspaces/{workspace.id}/operator/runs", headers=auth(user), json=payload
    )
    replay = client.post(
        f"/api/v1/workspaces/{workspace.id}/operator/runs", headers=auth(user), json=payload
    )
    current = client.get(f"/api/v1/workspaces/{workspace.id}/plans/current", headers=auth(user))

    assert first.status_code == replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["status"] == "completed"
    assert first.json()["provenance"] == {
        "analyzer": "approved-evidence",
        "version": "1",
        "mode": "deterministic",
    }
    assert current.status_code == 200
    assert current.json()["operatorRunId"] == first.json()["id"]
    assert len(current.json()["actions"]) == 1
    action = current.json()["actions"][0]
    assert action["evidenceIds"] == [str(evidence.id)]
    assert action["confidence"] == "high"
    assert action["missingInformation"] == []
    assert action["approvalStatus"] == "pending"
    opportunity_id = action["opportunityId"]
    for path in (
        "operator/runs",
        f"operator/runs/{first.json()['id']}",
        "plans",
        f"plans/{current.json()['id']}",
        f"plans/{current.json()['id']}/actions/{action['id']}",
        f"opportunities/{opportunity_id}",
    ):
        assert (
            client.get(f"/api/v1/workspaces/{workspace.id}/{path}", headers=auth(user)).status_code
            == 200
        )


def test_run_with_insufficient_approved_evidence_creates_no_recommendation(
    operator_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = operator_client
    assert client.portal is not None
    user, workspace, _, product, evidence = client.portal.call(seed_operator, sessions)

    async def reject_evidence() -> None:
        async with sessions() as db:
            item = await db.get(EvidenceItem, evidence.id)
            assert item is not None
            item.status = "rejected"
            await db.commit()

    client.portal.call(reject_evidence)
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/operator/runs",
        headers=auth(user),
        json={"productId": str(product.id), "idempotencyKey": "weekly:no-evidence"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    assert response.json()["summary"]["createdOpportunities"] == 0
    assert response.json()["summary"]["missingInformation"] == ["approved evidence"]


def test_gated_action_cannot_execute_until_approved_and_execution_is_idempotent(
    operator_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = operator_client
    assert client.portal is not None
    user, workspace, _, product, _ = client.portal.call(seed_operator, sessions)
    headers = auth(user)
    run = client.post(
        f"/api/v1/workspaces/{workspace.id}/operator/runs",
        headers=headers,
        json={"productId": str(product.id), "idempotencyKey": "weekly:approval"},
    )
    plan = client.get(f"/api/v1/workspaces/{workspace.id}/plans/current", headers=headers)
    action = plan.json()["actions"][0]
    execute_url = (
        f"/api/v1/workspaces/{workspace.id}/plans/{plan.json()['id']}"
        f"/actions/{action['id']}/execute"
    )

    blocked = client.post(execute_url, headers=headers, json={"idempotencyKey": "execute:1"})
    approved = client.post(
        f"/api/v1/workspaces/{workspace.id}/plans/{plan.json()['id']}"
        f"/actions/{action['id']}/approve",
        headers=headers,
        json={"reason": "Reviewed evidence."},
    )
    executed = client.post(execute_url, headers=headers, json={"idempotencyKey": "execute:1"})
    replay = client.post(execute_url, headers=headers, json={"idempotencyKey": "execute:1"})

    assert run.status_code == 201
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "action_approval_required"
    assert approved.status_code == 200
    assert executed.status_code == replay.status_code == 201
    assert replay.json()["id"] == executed.json()["id"]
    assert executed.json()["status"] == "completed"
    assert executed.json()["provenance"]["executor"] == "deterministic-local"


def test_execution_requires_ready_action_and_rejects_changed_key_replays(
    operator_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = operator_client
    assert client.portal is not None
    user, workspace, _, product, _ = client.portal.call(seed_operator, sessions)
    headers = auth(user)
    client.post(
        f"/api/v1/workspaces/{workspace.id}/operator/runs",
        headers=headers,
        json={"productId": str(product.id), "idempotencyKey": "weekly:execution-state"},
    )
    plan = client.get(f"/api/v1/workspaces/{workspace.id}/plans/current", headers=headers).json()
    action = plan["actions"][0]
    execute_url = (
        f"/api/v1/workspaces/{workspace.id}/plans/{plan['id']}/actions/{action['id']}/execute"
    )

    pending = client.post(execute_url, headers=headers, json={"idempotencyKey": "execute:pending"})
    client.post(
        f"/api/v1/workspaces/{workspace.id}/plans/{plan['id']}/actions/{action['id']}/approve",
        headers=headers,
        json={"reason": "Ready to execute."},
    )
    first = client.post(execute_url, headers=headers, json={"idempotencyKey": "execute:first"})
    changed_key = client.post(
        execute_url, headers=headers, json={"idempotencyKey": "execute:changed"}
    )

    assert pending.status_code == 409
    assert pending.json()["code"] == "action_approval_required"
    assert first.status_code == 201
    assert changed_key.status_code == 409
    assert changed_key.json()["code"] == "action_already_executed"


def test_cookie_authenticated_operator_mutation_requires_csrf(
    operator_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = operator_client
    assert client.portal is not None
    user, workspace, _, _, evidence = client.portal.call(seed_operator, sessions)

    async def add_session() -> AuthSession:
        async with sessions() as db:
            session = AuthSession(
                user_id=user.id,
                current_workspace_id=workspace.id,
                refresh_token_hash=uuid4().hex,
                expires_at=datetime.now(UTC) + timedelta(days=1),
                family_id=uuid4(),
            )
            db.add(session)
            await db.commit()
            return session

    session = client.portal.call(add_session)
    client.cookies.set(
        "access_token", create_access_token(str(user.id), session_id=str(session.id)), path="/"
    )
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/opportunities",
        json=recommendation_payload(evidence.id),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_origin"


async def test_all_operator_resources_require_non_nullable_workspace_scope() -> None:
    for model in (
        Opportunity,
        MarketingPlan,
        PlanAction,
        ApprovalRequest,
        ExecutionRun,
        VerificationRun,
        MeasurementWindow,
        OperatorRun,
    ):
        assert model.workspace_id.property.columns[0].nullable is False


async def test_operator_records_persist_all_foundation_resource_types(
    operator_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = operator_client
    assert client.portal is not None
    user, workspace, other_workspace, product, _ = client.portal.call(seed_operator, sessions)
    headers = auth(user)
    client.post(
        f"/api/v1/workspaces/{workspace.id}/operator/runs",
        headers=headers,
        json={"productId": str(product.id), "idempotencyKey": "weekly:records"},
    )
    plan = client.get(f"/api/v1/workspaces/{workspace.id}/plans/current", headers=headers).json()
    action = plan["actions"][0]
    client.post(
        f"/api/v1/workspaces/{workspace.id}/plans/{plan['id']}/actions/{action['id']}/approve",
        headers=headers,
        json={"reason": None},
    )
    client.post(
        f"/api/v1/workspaces/{workspace.id}/plans/{plan['id']}/actions/{action['id']}/execute",
        headers=headers,
        json={"idempotencyKey": "execute:records"},
    )

    assert (
        len(
            client.get(
                f"/api/v1/workspaces/{workspace.id}/plans/{plan['id']}/actions", headers=headers
            ).json()
        )
        == 1
    )
    for resource in ("approval-requests", "executions", "verifications", "measurements"):
        listed = client.get(f"/api/v1/workspaces/{workspace.id}/{resource}", headers=headers)
        assert listed.status_code == 200
        item = listed.json()[0]
        fetched = client.get(
            f"/api/v1/workspaces/{workspace.id}/{resource}/{item['id']}", headers=headers
        )
        assert fetched.status_code == 200
        assert fetched.json()["id"] == item["id"]

    async with sessions() as db:
        assert await list_resources(db, other_workspace.id, user.id, Opportunity) is None
        assert await get_resource(db, other_workspace.id, uuid4(), user.id, Opportunity) is None
        assert await get_plan_with_actions(db, workspace.id, uuid4(), user.id) is None
        assert len(list(await db.scalars(select(OperatorRun)))) == 1
        assert len(list(await db.scalars(select(Opportunity)))) == 1
        assert len(list(await db.scalars(select(MarketingPlan)))) == 1
        assert len(list(await db.scalars(select(PlanAction)))) == 1
        assert len(list(await db.scalars(select(ApprovalRequest)))) == 1
        assert len(list(await db.scalars(select(ExecutionRun)))) == 1
        assert len(list(await db.scalars(select(VerificationRun)))) == 1
        measurement = (await db.scalars(select(MeasurementWindow))).one()
        assert measurement.ends_at - measurement.starts_at == timedelta(days=28)
