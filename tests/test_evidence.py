from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.modules.evidence.api.router import router as evidence_router
from app.modules.evidence.models import Claim, ClaimEvidenceLink, EvidenceExcerpt, EvidenceItem
from app.modules.identity.models.oauth import AuthSession
from app.modules.identity.models.users import User
from app.modules.products.models import Product
from app.modules.workspaces.models import Workspace, WorkspaceMembership, WorkspaceRole


@pytest.fixture
def evidence_client() -> Generator[tuple[TestClient, async_sessionmaker[AsyncSession]], None, None]:
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
    app.include_router(evidence_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, sessions
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


async def seed_workspace(
    sessions: async_sessionmaker[AsyncSession], role: WorkspaceRole = WorkspaceRole.editor
) -> tuple[User, Workspace, Workspace, Product]:
    async with sessions() as db:
        user = User(email=f"{role.value}-{uuid4()}@example.com")
        workspace = Workspace(name="Evidence workspace")
        other_workspace = Workspace(name="Other workspace")
        db.add_all([user, workspace, other_workspace])
        await db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=user.id, role=role, is_active=True
            )
        )
        product = Product(workspace_id=workspace.id, user_id=user.id, name="Product")
        db.add(product)
        await db.commit()
        return user, workspace, other_workspace, product


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (
            {
                "evidenceType": "manual_note",
                "title": "Launch result",
                "content": "The release reduced setup time.",
                "publicSafeSummary": "Setup became faster.",
                "observedAt": "2026-07-20T10:00:00Z",
            },
            "manual_note",
        ),
        (
            {
                "evidenceType": "url",
                "title": "Public release",
                "content": "The public changelog documents the release.",
                "sourceUrl": "https://example.com/changelog/release",
                "observedAt": "2026-07-20T10:00:00Z",
            },
            "url",
        ),
        (
            {
                "evidenceType": "customer_quote",
                "title": "Customer feedback",
                "content": "This saved our team hours each week.",
                "sourceReference": "Interview 2026-07",
                "observedAt": "2026-07-20T10:00:00Z",
                "classification": "restricted",
            },
            "customer_quote",
        ),
    ],
)
def test_create_list_and_get_supported_evidence_with_safe_camel_case_contract(
    evidence_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
    payload: dict[str, object],
    expected_type: str,
) -> None:
    client, sessions = evidence_client
    assert client.portal is not None
    user, workspace, _, product = client.portal.call(seed_workspace, sessions)
    payload["productId"] = str(product.id)

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/evidence", headers=auth(user), json=payload
    )

    assert created.status_code == 201
    body = created.json()
    assert body["evidenceType"] == expected_type
    assert body["workspaceId"] == str(workspace.id)
    assert body["productId"] == str(product.id)
    assert body["status"] == "pending"
    assert body["freshnessStatus"] == "current"
    assert body["provenance"]["sourceType"] == expected_type
    assert "createdByActorId" not in body
    assert "rawPayload" not in body
    assert "credentials" not in body

    listed = client.get(f"/api/v1/workspaces/{workspace.id}/evidence", headers=auth(user))
    fetched = client.get(
        f"/api/v1/workspaces/{workspace.id}/evidence/{body['id']}", headers=auth(user)
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [body["id"]]
    assert fetched.status_code == 200
    assert fetched.json() == body


@pytest.mark.parametrize("role", [WorkspaceRole.reviewer, WorkspaceRole.viewer])
def test_read_only_roles_cannot_create_evidence(
    evidence_client: tuple[TestClient, async_sessionmaker[AsyncSession]], role: WorkspaceRole
) -> None:
    client, sessions = evidence_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_workspace, sessions, role)

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/evidence",
        headers=auth(user),
        json={
            "evidenceType": "manual_note",
            "title": "Note",
            "content": "A sufficiently useful note.",
            "observedAt": "2026-07-20T10:00:00Z",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "evidence_write_forbidden"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "evidenceType": "url",
            "title": "Missing URL",
            "content": "URL evidence requires provenance.",
            "observedAt": "2026-07-20T10:00:00Z",
        },
        {
            "evidenceType": "url",
            "title": "Credential URL",
            "content": "Credentials must not be accepted.",
            "sourceUrl": "https://user:secret@example.com/private",
            "observedAt": "2026-07-20T10:00:00Z",
        },
        {
            "evidenceType": "url",
            "title": "Private URL",
            "content": "Private network URLs must not be fetched.",
            "sourceUrl": "http://127.0.0.1/private",
            "observedAt": "2026-07-20T10:00:00Z",
        },
        {
            "evidenceType": "url",
            "title": "Token URL",
            "content": "Credential-bearing query parameters must not be stored.",
            "sourceUrl": "https://example.com/private?access_token=secret",
            "observedAt": "2026-07-20T10:00:00Z",
        },
    ],
)
def test_evidence_input_rejects_missing_or_unsafe_url_provenance(
    evidence_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
    payload: dict[str, object],
) -> None:
    client, sessions = evidence_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_workspace, sessions)

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/evidence", headers=auth(user), json=payload
    )

    assert response.status_code == 422


def test_evidence_access_hides_cross_workspace_and_cross_product_resources(
    evidence_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = evidence_client
    assert client.portal is not None
    user, workspace, other_workspace, product = client.portal.call(seed_workspace, sessions)
    headers = auth(user)
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/evidence",
        headers=headers,
        json={
            "evidenceType": "manual_note",
            "title": "Scoped note",
            "content": "This belongs to one workspace.",
            "observedAt": "2026-07-20T10:00:00Z",
        },
    ).json()

    hidden = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/evidence/{created['id']}", headers=headers
    )
    bad_product = client.post(
        f"/api/v1/workspaces/{workspace.id}/evidence",
        headers=headers,
        json={
            "evidenceType": "manual_note",
            "title": "Bad product",
            "content": "The product belongs elsewhere or does not exist.",
            "observedAt": "2026-07-20T10:00:00Z",
            "productId": str(uuid4()),
        },
    )

    assert hidden.status_code == 404
    assert hidden.json()["code"] == "evidence_not_found"
    assert bad_product.status_code == 404
    assert bad_product.json()["code"] == "evidence_product_not_found"
    assert product.workspace_id == workspace.id


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (WorkspaceRole.owner, True),
        (WorkspaceRole.admin, True),
        (WorkspaceRole.reviewer, True),
        (WorkspaceRole.editor, False),
        (WorkspaceRole.viewer, False),
    ],
)
def test_review_roles_approve_or_reject_with_auditable_provenance(
    evidence_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
    role: WorkspaceRole,
    allowed: bool,
) -> None:
    client, sessions = evidence_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_workspace, sessions, role)

    async def add_pending() -> EvidenceItem:
        async with sessions() as db:
            item = EvidenceItem(
                workspace_id=workspace.id,
                evidence_type="manual_note",
                title="Pending note",
                content="Evidence awaiting a review decision.",
                source_type="manual_note",
                observed_at=datetime.now(UTC),
                created_by_actor_id=user.id,
            )
            db.add(item)
            await db.commit()
            return item

    item = client.portal.call(add_pending)
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/evidence/{item.id}/approve",
        headers=auth(user),
        json={"reason": "Source and wording verified."},
    )

    assert response.status_code == (200 if allowed else 403)
    if allowed:
        body = response.json()
        assert body["status"] == "approved"
        assert body["latestReview"]["decision"] == "approved"
        assert body["latestReview"]["reviewerId"] == str(user.id)
        assert body["lastVerifiedAt"] is not None
    else:
        assert response.json()["code"] == "evidence_review_forbidden"


def test_reject_requires_reason_and_prevents_invalid_second_decision(
    evidence_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = evidence_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_workspace, sessions, WorkspaceRole.reviewer)

    async def add_pending() -> EvidenceItem:
        async with sessions() as db:
            item = EvidenceItem(
                workspace_id=workspace.id,
                evidence_type="customer_quote",
                title="Quote",
                content="A quote requiring consent confirmation.",
                source_type="customer_quote",
                observed_at=datetime.now(UTC),
                created_by_actor_id=user.id,
            )
            db.add(item)
            await db.commit()
            return item

    item = client.portal.call(add_pending)
    missing_reason = client.post(
        f"/api/v1/workspaces/{workspace.id}/evidence/{item.id}/reject",
        headers=auth(user),
        json={"reason": ""},
    )
    rejected = client.post(
        f"/api/v1/workspaces/{workspace.id}/evidence/{item.id}/reject",
        headers=auth(user),
        json={"reason": "Customer consent is not recorded."},
    )
    repeated = client.post(
        f"/api/v1/workspaces/{workspace.id}/evidence/{item.id}/approve",
        headers=auth(user),
        json={"reason": "Changed mind."},
    )

    assert missing_reason.status_code == 422
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "evidence_already_reviewed"


def test_list_claims_returns_only_tenant_claims_with_linked_evidence_excerpts(
    evidence_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = evidence_client
    assert client.portal is not None
    user, workspace, other_workspace, product = client.portal.call(seed_workspace, sessions)

    async def add_claims() -> None:
        async with sessions() as db:
            evidence = EvidenceItem(
                workspace_id=workspace.id,
                product_id=product.id,
                evidence_type="url",
                title="Release notes",
                content="Release notes document a faster setup flow.",
                source_type="url",
                source_url="https://example.com/releases/setup",
                observed_at=datetime.now(UTC),
                status="approved",
                created_by_actor_id=user.id,
            )
            db.add(evidence)
            await db.flush()
            excerpt = EvidenceExcerpt(
                workspace_id=workspace.id,
                evidence_item_id=evidence.id,
                text="Setup now takes less time.",
                created_by_actor_id=user.id,
            )
            claim = Claim(
                workspace_id=workspace.id,
                product_id=product.id,
                statement="Setup is faster.",
                status="approved",
                created_by_actor_id=user.id,
            )
            hidden = Claim(
                workspace_id=other_workspace.id,
                statement="Hidden claim.",
                created_by_actor_id=user.id,
            )
            db.add_all([excerpt, claim, hidden])
            await db.flush()
            db.add(
                ClaimEvidenceLink(
                    workspace_id=workspace.id,
                    claim_id=claim.id,
                    evidence_excerpt_id=excerpt.id,
                    created_by_actor_id=user.id,
                )
            )
            await db.commit()

    client.portal.call(add_claims)
    response = client.get(f"/api/v1/workspaces/{workspace.id}/claims", headers=auth(user))

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    claim = response.json()["items"][0]
    assert claim["statement"] == "Setup is faster."
    assert claim["status"] == "approved"
    assert claim["linkedEvidence"][0]["excerpt"] == "Setup now takes less time."
    assert claim["linkedEvidence"][0]["evidenceStatus"] == "approved"
    assert "content" not in claim["linkedEvidence"][0]


def test_expired_evidence_reports_expired_freshness_without_mutating_review_status(
    evidence_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = evidence_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_workspace, sessions)

    async def add_expired() -> EvidenceItem:
        async with sessions() as db:
            item = EvidenceItem(
                workspace_id=workspace.id,
                evidence_type="manual_note",
                title="Old benchmark",
                content="A once-approved benchmark is now expired.",
                source_type="manual_note",
                observed_at=datetime.now(UTC) - timedelta(days=90),
                expires_at=datetime.now(UTC) - timedelta(days=1),
                status="approved",
                created_by_actor_id=user.id,
            )
            db.add(item)
            await db.commit()
            return item

    item = client.portal.call(add_expired)
    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/evidence/{item.id}", headers=auth(user)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["freshnessStatus"] == "expired"


@pytest.mark.parametrize("classification", ["confidential", "blocked"])
def test_viewers_receive_only_safe_metadata_for_sensitive_evidence(
    evidence_client: tuple[TestClient, async_sessionmaker[AsyncSession]], classification: str
) -> None:
    client, sessions = evidence_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_workspace, sessions, WorkspaceRole.viewer)

    async def add_sensitive_evidence() -> EvidenceItem:
        async with sessions() as db:
            item = EvidenceItem(
                workspace_id=workspace.id,
                evidence_type="manual_note",
                title="Private customer incident",
                content="The customer name and incident details must not be disclosed.",
                public_safe_summary="A non-public issue was observed.",
                what_happened="Sensitive implementation details.",
                why_it_matters="Sensitive commercial impact.",
                source_type="manual_note",
                source_reference="private-interview",
                observed_at=datetime.now(UTC),
                classification=classification,
                status="approved",
                created_by_actor_id=user.id,
            )
            db.add(item)
            await db.commit()
            return item

    item = client.portal.call(add_sensitive_evidence)
    listed = client.get(f"/api/v1/workspaces/{workspace.id}/evidence", headers=auth(user))
    fetched = client.get(
        f"/api/v1/workspaces/{workspace.id}/evidence/{item.id}", headers=auth(user)
    )

    assert listed.status_code == fetched.status_code == 200
    for response in (listed.json()["items"][0], fetched.json()):
        assert response["title"] == "Restricted evidence"
        assert response["content"] is None
        assert response["whatHappened"] is None
        assert response["whyItMatters"] is None
        assert response["provenance"]["sourceReference"] is None
        assert response["publicSafeSummary"] == "A non-public issue was observed."


def test_cookie_authenticated_evidence_mutation_requires_csrf(
    evidence_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = evidence_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_workspace, sessions)

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
        f"/api/v1/workspaces/{workspace.id}/evidence",
        json={
            "evidenceType": "manual_note",
            "title": "Cookie write",
            "content": "Cookie-authenticated writes require CSRF validation.",
            "observedAt": "2026-07-20T10:00:00Z",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_origin"
