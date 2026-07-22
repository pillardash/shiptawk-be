from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token, sign_csrf_token
from app.db.base import Base
from app.db.session import get_db
from app.domains.auth.models import AuthSession
from app.domains.drafts.generation import _ensure_allowed, _safety_settings, _voice_profile
from app.domains.drafts.repository import (
    get_draft_review_for_workspace,
    list_drafts_for_workspace,
)
from app.domains.drafts.router import router as drafts_router
from app.domains.generation.fake_provider import DeterministicGenerationProvider
from app.domains.generation.models import GenerationTelemetry, ProviderGenerationResult
from app.domains.integrations.event_normalizer import NormalizedEvent
from app.domains.legacy.models import (
    draft_feedback_events,
    draft_status_events,
    drafts,
    event_evaluations,
    generation_runs,
    github_raw_event_consumers,
    github_raw_events,
    normalized_events,
    repos,
    tweet_candidates,
)
from app.domains.users.models import User
from app.domains.workspaces.models import Workspace, WorkspaceMembership, WorkspaceRole
from app.main import create_app
from app.shared.exceptions import ConflictError


@pytest.fixture
def draft_client() -> Generator[tuple[TestClient, async_sessionmaker[AsyncSession]], None, None]:
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
    app.include_router(drafts_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, sessions
        client.portal.call(drop_tables)
        client.portal.call(engine.dispose)


async def seed_drafts(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[User, Workspace, Workspace, list[UUID]]:
    async with sessions() as db:
        user = User(email="draft-reviewer@example.com")
        workspace = Workspace(name="Draft workspace")
        other_workspace = Workspace(name="Other workspace")
        db.add_all([user, workspace, other_workspace])
        await db.flush()
        db.add_all(
            [
                WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, is_active=True),
                WorkspaceMembership(
                    workspace_id=other_workspace.id, user_id=user.id, is_active=True
                ),
            ]
        )
        repo_id = uuid4()
        other_repo_id = uuid4()
        await db.execute(
            insert(repos),
            [
                {
                    "id": repo_id,
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "repo_name": "app",
                    "repo_full_name": "example/app",
                },
                {
                    "id": other_repo_id,
                    "workspace_id": other_workspace.id,
                    "user_id": user.id,
                    "repo_name": "private",
                    "repo_full_name": "example/private",
                },
            ],
        )
        created_at = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
        draft_ids = [
            UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"),
            UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2"),
            UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3"),
        ]
        await db.execute(
            insert(drafts),
            [
                {
                    "id": draft_ids[0],
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "repo_id": repo_id,
                    "content": "Older pending draft",
                    "source_event_type": "push",
                    "source_event_payload": {"privateSource": "do not return"},
                    "status": "pending",
                    "created_at": created_at,
                    "posting_started_at": created_at,
                },
                {
                    "id": draft_ids[1],
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "repo_id": repo_id,
                    "content": "Stable tie draft",
                    "source_event_type": "release",
                    "source_event_payload": {"prompt": "do not return"},
                    "status": "pending",
                    "created_at": created_at,
                    "posting_started_at": None,
                },
                {
                    "id": draft_ids[2],
                    "workspace_id": other_workspace.id,
                    "user_id": user.id,
                    "repo_id": other_repo_id,
                    "content": "Cross-workspace draft",
                    "source_event_type": "push",
                    "source_event_payload": {},
                    "status": "rejected",
                    "created_at": created_at,
                    "posting_started_at": None,
                },
            ],
        )
        await db.commit()
        return user, workspace, other_workspace, draft_ids


async def seed_review(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[User, Workspace, Workspace, UUID, list[UUID]]:
    user, workspace, other_workspace, draft_ids = await seed_drafts(sessions)
    async with sessions() as db:
        raw_event_id = uuid4()
        consumer_id = uuid4()
        normalized_event_id = uuid4()
        await db.execute(
            insert(github_raw_events).values(
                id=raw_event_id,
                github_delivery_id=f"delivery-{raw_event_id}",
                event_name="push",
                repo_full_name="example/app",
                payload_ciphertext="encrypted-private-payload",
                payload_key_version="v1",
            )
        )
        repo_id = await db.scalar(
            repos.select().with_only_columns(repos.c.id).where(repos.c.workspace_id == workspace.id)
        )
        assert repo_id is not None
        await db.execute(
            insert(github_raw_event_consumers).values(
                id=consumer_id,
                workspace_id=workspace.id,
                raw_event_id=raw_event_id,
                repo_id=repo_id,
                user_id=user.id,
            )
        )
        await db.execute(
            insert(normalized_events).values(
                id=normalized_event_id,
                workspace_id=workspace.id,
                user_id=user.id,
                raw_event_id=raw_event_id,
                repo_id=repo_id,
                event_type="push",
                repo_full_name="example/app",
                title="Review API",
                description="Added tenant-safe reads",
                url="https://example.test/change",
                occurred_at=datetime(2026, 7, 22, 11, 0, tzinfo=UTC),
                dedupe_key=f"event-{normalized_event_id}",
            )
        )
        await db.execute(
            drafts.update()
            .where(drafts.c.id == draft_ids[1])
            .values(normalized_event_id=normalized_event_id)
        )
        await db.execute(
            insert(event_evaluations).values(
                id=uuid4(),
                workspace_id=workspace.id,
                user_id=user.id,
                normalized_event_id=normalized_event_id,
                repo_id=repo_id,
                public_worthiness_score=88,
                is_public_worthy=True,
                rationale="Useful public product update",
                failed_safety_checks=[],
                outcome="generate",
                dimension_scores={"customer_value": 9},
                internal_summary="Internal implementation details",
                public_summary="Draft review is safer",
                confidence=91,
            )
        )
        candidate_ids = [uuid4(), uuid4()]
        await db.execute(
            insert(tweet_candidates),
            [
                {
                    "id": candidate_ids[0],
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "normalized_event_id": normalized_event_id,
                    "repo_id": repo_id,
                    "content": "Second option",
                    "rank": 2,
                    "score": 75,
                    "rationale": "Specific",
                },
                {
                    "id": candidate_ids[1],
                    "workspace_id": workspace.id,
                    "user_id": user.id,
                    "normalized_event_id": normalized_event_id,
                    "repo_id": repo_id,
                    "content": "First option",
                    "rank": 1,
                    "score": 90,
                    "rationale": "Clear customer value",
                },
            ],
        )
        await db.commit()
    return user, workspace, other_workspace, draft_ids[1], candidate_ids


def auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def set_role(
    sessions: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
    user_id: UUID,
    role: WorkspaceRole,
) -> None:
    async with sessions() as db:
        membership = await db.get(
            WorkspaceMembership, {"workspace_id": workspace_id, "user_id": user_id}
        )
        assert membership is not None
        membership.role = role
        await db.commit()


async def draft_command_state(
    sessions: async_sessionmaker[AsyncSession], draft_id: UUID
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    async with sessions() as db:
        draft = (await db.execute(drafts.select().where(drafts.c.id == draft_id))).mappings().one()
        statuses = (
            await db.execute(
                draft_status_events.select().where(draft_status_events.c.draft_id == draft_id)
            )
        ).mappings()
        feedback = (
            await db.execute(
                draft_feedback_events.select().where(draft_feedback_events.c.draft_id == draft_id)
            )
        ).mappings()
        return dict(draft), [dict(row) for row in statuses], [dict(row) for row in feedback]


def test_list_drafts_is_paginated_stable_and_sanitized(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_ids = client.portal.call(seed_drafts, sessions)

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/drafts?status=pending",
        headers=auth_headers(user),
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["data"]] == [str(draft_ids[1]), str(draft_ids[0])]
    assert body["pagination"] == {
        "page": 1,
        "limit": 20,
        "total": 2,
        "totalPages": 1,
        "hasNext": False,
        "hasPrev": False,
    }
    assert body["data"][0]["workspaceId"] == str(workspace.id)
    assert {item["repoFullName"] for item in body["data"]} == {"example/app"}
    for item in body["data"]:
        assert "userId" not in item
        assert "sourceEventPayload" not in item
        assert "postingStartedAt" not in item

    second_page = client.get(
        f"/api/v1/workspaces/{workspace.id}/drafts?status=pending&page=2&limit=1",
        headers=auth_headers(user),
    ).json()
    assert [item["id"] for item in second_page["data"]] == [str(draft_ids[0])]
    assert second_page["pagination"] == {
        "page": 2,
        "limit": 1,
        "total": 2,
        "totalPages": 2,
        "hasNext": False,
        "hasPrev": True,
    }


@pytest.mark.parametrize(
    "query",
    ["status=unknown", "page=0", "page=one", "limit=0", "limit=101", "limit=many"],
)
def test_list_drafts_rejects_invalid_queries(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]], query: str
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_drafts, sessions)

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/drafts?{query}", headers=auth_headers(user)
    )

    assert response.status_code == 422


def test_list_drafts_requires_active_workspace_membership(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, _ = client.portal.call(seed_drafts, sessions)

    async def deactivate() -> None:
        async with sessions() as db:
            membership = await db.get(
                WorkspaceMembership, {"workspace_id": workspace.id, "user_id": user.id}
            )
            assert membership is not None
            membership.is_active = False
            await db.commit()

    client.portal.call(deactivate)
    response = client.get(f"/api/v1/workspaces/{workspace.id}/drafts", headers=auth_headers(user))

    assert response.status_code == 404
    assert response.json()["code"] == "draft_workspace_not_found"


def test_review_returns_sanitized_tenant_scoped_data_ordered_by_rank(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_id, candidate_ids = client.portal.call(seed_review, sessions)

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/review",
        headers=auth_headers(user),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["draft"]["id"] == str(draft_id)
    assert data["draft"]["repoFullName"] == "example/app"
    assert data["evaluation"]["publicWorthinessScore"] == 88
    assert [candidate["id"] for candidate in data["candidates"]] == [
        str(candidate_ids[1]),
        str(candidate_ids[0]),
    ]
    forbidden = {
        "userId",
        "sourceEventPayload",
        "postingStartedAt",
        "systemPrompt",
        "userPrompt",
        "contextSnapshot",
        "candidateSnapshot",
    }
    assert forbidden.isdisjoint(data["draft"])
    assert forbidden.isdisjoint(data["evaluation"])
    assert all(forbidden.isdisjoint(candidate) for candidate in data["candidates"])


def test_review_without_normalized_event_returns_empty_review_context(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_ids = client.portal.call(seed_drafts, sessions)

    response = client.get(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_ids[0]}/review",
        headers=auth_headers(user),
    )

    assert response.status_code == 200
    assert response.json()["data"]["evaluation"] is None
    assert response.json()["data"]["candidates"] == []


def test_review_does_not_resolve_a_draft_through_another_workspace(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, other_workspace, draft_id, _ = client.portal.call(seed_review, sessions)

    cross_workspace = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/drafts/{draft_id}/review",
        headers=auth_headers(user),
    )
    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/drafts/{uuid4()}/review",
        headers=auth_headers(user),
    )

    assert cross_workspace.status_code == missing.status_code == 404
    for response in (cross_workspace, missing):
        assert response.json()["detail"] == "Draft not found."
        assert response.json()["code"] == "draft_not_found"


def test_repository_applies_workspace_scope_to_all_draft_reads(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, other_workspace, draft_id, _ = client.portal.call(seed_review, sessions)

    async def read_repository() -> None:
        async with sessions() as db:
            listed = await list_drafts_for_workspace(
                db, workspace.id, user.id, status=None, page=1, limit=100
            )
            assert listed is not None
            assert listed[1] == 2
            assert {draft["repo_full_name"] for draft in listed[0]} == {"example/app"}
            assert all("source_event_payload" not in draft for draft in listed[0])

            review = await get_draft_review_for_workspace(db, workspace.id, draft_id, user.id)
            assert review is not None
            assert review[0]["repo_full_name"] == "example/app"
            assert "source_event_payload" not in review[0]
            assert review[1] is not None
            assert [candidate["rank"] for candidate in review[2]] == [1, 2]

            assert (
                await get_draft_review_for_workspace(db, other_workspace.id, draft_id, user.id)
                is None
            )

    client.portal.call(read_repository)


def test_repository_does_not_join_drafts_to_repos_across_workspaces(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, other_workspace, draft_ids = client.portal.call(seed_drafts, sessions)

    async def replace_repo_with_cross_workspace_repo() -> None:
        async with sessions() as db:
            other_repo_id = await db.scalar(
                repos.select()
                .with_only_columns(repos.c.id)
                .where(repos.c.workspace_id == other_workspace.id)
            )
            assert other_repo_id is not None
            await db.execute(
                drafts.update().where(drafts.c.id == draft_ids[0]).values(repo_id=other_repo_id)
            )
            await db.commit()

    async def read_repository() -> None:
        async with sessions() as db:
            listed = await list_drafts_for_workspace(
                db, workspace.id, user.id, status=None, page=1, limit=100
            )
            assert listed is not None
            assert listed[1] == 1
            assert [draft["id"] for draft in listed[0]] == [draft_ids[1]]
            assert (
                await get_draft_review_for_workspace(db, workspace.id, draft_ids[0], user.id)
                is None
            )

    client.portal.call(replace_repo_with_cross_workspace_repo)
    client.portal.call(read_repository)


def test_editor_can_edit_and_select_candidate_with_feedback_history(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_id, candidate_ids = client.portal.call(seed_review, sessions)
    client.portal.call(set_role, sessions, workspace.id, user.id, WorkspaceRole.editor)
    headers = auth_headers(user)

    edited = client.patch(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/content",
        headers=headers,
        json={"content": "A safer edited option"},
    )
    selected = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/select-candidate",
        headers=headers,
        json={"candidateId": str(candidate_ids[0])},
    )

    assert edited.status_code == selected.status_code == 200
    assert edited.json()["content"] == "A safer edited option"
    assert selected.json()["content"] == "Second option"
    assert selected.json()["selectedCandidateId"] == str(candidate_ids[0])
    assert "sourceEventPayload" not in selected.json()
    draft, statuses, feedback = client.portal.call(draft_command_state, sessions, draft_id)
    assert draft["status"] == "pending"
    assert draft["user_edited"] is False
    assert draft["source_event_payload"] == {"prompt": "do not return"}
    assert statuses == []
    assert [event["event_type"] for event in feedback] == ["edited", "selected"]
    assert feedback[0]["content_before"] == "Stable tie draft"
    assert feedback[0]["content_after"] == "A safer edited option"
    assert feedback[1]["previous_candidate_id"] is None
    assert all("do not return" not in str(event["metadata"]) for event in feedback)


def test_reviewer_can_approve_and_reject_but_cannot_edit_or_select(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_id, candidate_ids = client.portal.call(seed_review, sessions)
    client.portal.call(set_role, sessions, workspace.id, user.id, WorkspaceRole.reviewer)
    headers = auth_headers(user)

    edit = client.patch(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/content",
        headers=headers,
        json={"content": "Reviewer rewrite"},
    )
    select_candidate = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/select-candidate",
        headers=headers,
        json={"candidateId": str(candidate_ids[0])},
    )
    approved = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/approve", headers=headers
    )
    rejected = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/reject",
        headers=headers,
        json={"reason": "The claim needs stronger public evidence."},
    )

    assert edit.status_code == select_candidate.status_code == 403
    assert edit.json()["code"] == select_candidate.json()["code"] == "draft_write_forbidden"
    assert approved.status_code == rejected.status_code == 200
    assert approved.json()["status"] == "approved"
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejectionReason"] == "The claim needs stronger public evidence."
    draft, statuses, feedback = client.portal.call(draft_command_state, sessions, draft_id)
    assert draft["approved_at"] is None
    assert draft["rejected_at"] is not None
    assert [event["status"] for event in statuses] == ["approved", "rejected"]
    assert [event["event_type"] for event in feedback] == ["approved", "rejected"]


def test_approve_and_reject_are_idempotent_without_duplicate_history(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_ids = client.portal.call(seed_drafts, sessions)
    client.portal.call(set_role, sessions, workspace.id, user.id, WorkspaceRole.owner)
    headers = auth_headers(user)
    approve_url = f"/api/v1/workspaces/{workspace.id}/drafts/{draft_ids[0]}/approve"
    reject_url = f"/api/v1/workspaces/{workspace.id}/drafts/{draft_ids[1]}/reject"

    first_approval = client.post(approve_url, headers=headers)
    second_approval = client.post(approve_url, headers=headers)
    first_rejection = client.post(
        reject_url, headers=headers, json={"reason": "Not enough evidence"}
    )
    second_rejection = client.post(
        reject_url, headers=headers, json={"reason": "A different retry reason"}
    )

    assert first_approval.status_code == second_approval.status_code == 200
    assert first_approval.json()["approvedAt"] == second_approval.json()["approvedAt"]
    assert first_rejection.status_code == second_rejection.status_code == 200
    assert second_rejection.json()["rejectionReason"] == "Not enough evidence"
    _, approval_statuses, approval_feedback = client.portal.call(
        draft_command_state, sessions, draft_ids[0]
    )
    _, rejection_statuses, rejection_feedback = client.portal.call(
        draft_command_state, sessions, draft_ids[1]
    )
    assert len(approval_statuses) == len(approval_feedback) == 1
    assert len(rejection_statuses) == len(rejection_feedback) == 1


def test_commands_enforce_terminal_lifecycle_without_publishing(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_ids = client.portal.call(seed_drafts, sessions)
    client.portal.call(set_role, sessions, workspace.id, user.id, WorkspaceRole.owner)

    async def set_terminal_states() -> None:
        now = datetime.now(UTC)
        async with sessions() as db:
            await db.execute(
                drafts.update()
                .where(drafts.c.id == draft_ids[0])
                .values(
                    status="posted",
                    approved_at=now,
                    posted_at=now,
                    tweet_id="existing-post-id",
                )
            )
            await db.execute(
                drafts.update()
                .where(drafts.c.id == draft_ids[1])
                .values(status="rejected", rejected_at=now, rejection_reason="Existing reason")
            )
            await db.commit()

    client.portal.call(set_terminal_states)
    headers = auth_headers(user)

    posted_approval = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_ids[0]}/approve", headers=headers
    )
    posted_rejection = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_ids[0]}/reject",
        headers=headers,
        json={"reason": "Do not mutate a post"},
    )
    rejected_edit = client.patch(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_ids[1]}/content",
        headers=headers,
        json={"content": "Do not revive a rejected draft"},
    )
    rejected_approval = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_ids[1]}/approve", headers=headers
    )

    assert posted_approval.status_code == 200
    assert posted_approval.json()["status"] == "posted"
    assert posted_approval.json()["tweetId"] == "existing-post-id"
    assert (
        posted_rejection.status_code
        == rejected_edit.status_code
        == rejected_approval.status_code
        == 409
    )
    posted, posted_statuses, posted_feedback = client.portal.call(
        draft_command_state, sessions, draft_ids[0]
    )
    rejected, rejected_statuses, rejected_feedback = client.portal.call(
        draft_command_state, sessions, draft_ids[1]
    )
    assert posted["tweet_id"] == "existing-post-id"
    assert rejected["rejection_reason"] == "Existing reason"
    assert posted_statuses == posted_feedback == rejected_statuses == rejected_feedback == []


def test_viewer_and_inactive_membership_cannot_run_commands(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_ids = client.portal.call(seed_drafts, sessions)
    headers = auth_headers(user)
    url = f"/api/v1/workspaces/{workspace.id}/drafts/{draft_ids[0]}/approve"

    viewer = client.post(url, headers=headers)

    async def deactivate() -> None:
        async with sessions() as db:
            membership = await db.get(
                WorkspaceMembership, {"workspace_id": workspace.id, "user_id": user.id}
            )
            assert membership is not None
            membership.is_active = False
            await db.commit()

    client.portal.call(deactivate)
    inactive = client.post(url, headers=headers)

    assert viewer.status_code == 403
    assert viewer.json()["code"] == "draft_review_forbidden"
    assert inactive.status_code == 404
    assert inactive.json()["code"] == "draft_not_found"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("content", {"content": "Cross-workspace edit"}),
        ("approve", None),
        ("reject", {"reason": "Cross-workspace rejection"}),
        ("select-candidate", {"candidateId": "00000000-0000-0000-0000-000000000001"}),
    ],
)
def test_commands_do_not_resolve_drafts_through_another_workspace(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
    path: str,
    payload: dict[str, str] | None,
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, other_workspace, draft_ids = client.portal.call(seed_drafts, sessions)
    client.portal.call(set_role, sessions, other_workspace.id, user.id, WorkspaceRole.owner)
    method = client.patch if path == "content" else client.post

    response = method(
        f"/api/v1/workspaces/{other_workspace.id}/drafts/{draft_ids[0]}/{path}",
        headers=auth_headers(user),
        json=payload,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "draft_not_found"
    draft, statuses, feedback = client.portal.call(draft_command_state, sessions, draft_ids[0])
    assert draft["workspace_id"] == workspace.id
    assert statuses == feedback == []


def test_candidate_selection_requires_same_workspace_and_event(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_id, _ = client.portal.call(seed_review, sessions)
    client.portal.call(set_role, sessions, workspace.id, user.id, WorkspaceRole.admin)

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/select-candidate",
        headers=auth_headers(user),
        json={"candidateId": str(uuid4())},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "draft_candidate_not_found"
    _, statuses, feedback = client.portal.call(draft_command_state, sessions, draft_id)
    assert statuses == feedback == []


def test_cookie_authenticated_command_requires_valid_csrf(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_ids = client.portal.call(seed_drafts, sessions)
    client.portal.call(set_role, sessions, workspace.id, user.id, WorkspaceRole.owner)

    async def add_session() -> AuthSession:
        async with sessions() as db:
            session = AuthSession(
                user_id=user.id,
                current_workspace_id=workspace.id,
                refresh_token_hash=f"draft-cookie-{uuid4()}",
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
    url = f"/api/v1/workspaces/{workspace.id}/drafts/{draft_ids[0]}/approve"

    missing = client.post(url, headers={"Origin": "http://localhost:3000"})
    csrf = sign_csrf_token(str(session.id))
    client.cookies.set("csrf_token", csrf, path="/")
    accepted = client.post(
        url,
        headers={"Origin": "http://localhost:3000", "X-CSRF-Token": csrf},
    )

    assert missing.status_code == 400
    assert missing.json()["code"] == "invalid_csrf_token"
    assert accepted.status_code == 200


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("content", {"content": ""}),
        ("content", {"content": "x" * 281}),
        ("reject", {"reason": "   "}),
        ("reject", {"reason": "x" * 281}),
    ],
)
def test_draft_commands_validate_public_text_limits(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
    path: str,
    payload: dict[str, str],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, _, draft_ids = client.portal.call(seed_drafts, sessions)
    client.portal.call(set_role, sessions, workspace.id, user.id, WorkspaceRole.owner)
    method = client.patch if path == "content" else client.post

    response = method(
        f"/api/v1/workspaces/{workspace.id}/drafts/{draft_ids[0]}/{path}",
        headers=auth_headers(user),
        json=payload,
    )

    assert response.status_code == 422


def test_angle_regeneration_is_idempotent_persisted_and_requires_reapproval(
    draft_client: tuple[TestClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = draft_client
    assert client.portal is not None
    user, workspace, other_workspace, draft_id, _ = client.portal.call(seed_review, sessions)
    client.portal.call(set_role, sessions, workspace.id, user.id, WorkspaceRole.editor)
    client.portal.call(set_role, sessions, other_workspace.id, user.id, WorkspaceRole.owner)

    async def approve_existing() -> None:
        async with sessions() as db:
            await db.execute(
                repos.update().where(repos.c.workspace_id == workspace.id).values(is_tracked=True)
            )
            await db.execute(
                drafts.update()
                .where(drafts.c.id == draft_id)
                .values(status="approved", approved_at=datetime.now(UTC))
            )
            await db.commit()

    client.portal.call(approve_existing)
    provider = DeterministicGenerationProvider(
        result=ProviderGenerationResult(
            output={
                "candidates": [
                    {
                        "content": "Teams can now review safer tenant-scoped draft data.",
                        "variant": "customer_value",
                        "angle": "user_benefit",
                        "rationale": "Connects the shipped change to a user benefit.",
                        "evidenceIds": ["event.change.1", "event.change.2"],
                    }
                ]
            },
            telemetry=GenerationTelemetry(
                provider="deterministic",
                model="fake-v1",
                latency_ms=5,
                correlation_id="draft-correlation",
            ),
        )
    )
    app = cast(FastAPI, client.app)
    app.state.generation_provider = provider
    app.state.generation_model = "fake-v1"
    payload = {"angle": "user_benefit", "idempotencyKey": "draft-angle:test-1"}
    url = f"/api/v1/workspaces/{workspace.id}/drafts/{draft_id}/regenerate-angle"

    first = client.post(url, headers=auth_headers(user), json=payload)
    replay = client.post(url, headers=auth_headers(user), json=payload)
    hidden = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/drafts/{draft_id}/regenerate-angle",
        headers=auth_headers(user),
        json={**payload, "idempotencyKey": "draft-angle:hidden"},
    )

    assert first.status_code == 200, first.json()
    assert replay.status_code == 200, replay.json()
    assert first.json()["content"] == "Teams can now review safer tenant-scoped draft data."
    assert first.json()["status"] == "pending"
    assert first.json()["approvedAt"] is None
    assert first.json()["generationRunId"] == replay.json()["generationRunId"]
    assert provider.call_count == 1
    assert hidden.status_code == 404

    async def regeneration_state() -> tuple[int, int, int, str | None]:
        async with sessions() as db:
            run_count = await db.scalar(
                select(func.count())
                .select_from(generation_runs)
                .where(
                    generation_runs.c.workspace_id == workspace.id,
                    generation_runs.c.trigger == "angle_regeneration",
                )
            )
            candidate_count = await db.scalar(
                select(func.count())
                .select_from(tweet_candidates)
                .where(
                    tweet_candidates.c.generation_run_id == UUID(first.json()["generationRunId"])
                )
            )
            feedback_count = await db.scalar(
                select(func.count())
                .select_from(draft_feedback_events)
                .where(
                    draft_feedback_events.c.draft_id == draft_id,
                    draft_feedback_events.c.event_type == "selected",
                )
            )
            trigger = await db.scalar(
                select(generation_runs.c.requested_angle).where(
                    generation_runs.c.id == UUID(first.json()["generationRunId"])
                )
            )
            return run_count or 0, candidate_count or 0, feedback_count or 0, trigger

    assert client.portal.call(regeneration_state) == (1, 1, 1, "user_benefit")


def test_regeneration_uses_safe_defaults_for_malformed_user_preferences() -> None:
    user = User(
        email="generation-preferences@example.com",
        voice_profile={"tone_modes": "not-a-list", "audience_preference": 3},
        safety_settings={"max_drafts_per_week": "many", "private_repo_mode": "unknown"},
    )

    voice = _voice_profile(user)
    safety = _safety_settings(user)

    assert voice.tone_modes == []
    assert voice.audience_preference is None
    assert safety.preferred_event_types == ["push", "release", "pull_request_merged"]
    assert safety.max_drafts_per_week == 14
    assert safety.private_repo_mode == "normal"


@pytest.mark.parametrize(
    ("event", "context", "repo_private", "code"),
    [
        (
            NormalizedEvent(
                event_type="release",
                repo_full_name="example/app",
                title="Public",
                description="Update",
                url="https://example.test",
                occurred_at="2026-07-22T00:00:00Z",
                sha=None,
                branch_name=None,
                touched_paths=[],
                dedupe_key="event-type",
            ),
            type(
                "Context",
                (),
                {
                    "preferred_event_types": ["push"],
                    "private_repo_mode": "normal",
                    "blocked_terms": [],
                    "blocked_topics": [],
                    "ignored_paths": [],
                },
            )(),
            False,
            "draft_regeneration_event_disabled",
        ),
        (
            NormalizedEvent(
                event_type="push",
                repo_full_name="example/app",
                title="Public",
                description="Update",
                url="https://example.test",
                occurred_at="2026-07-22T00:00:00Z",
                sha=None,
                branch_name=None,
                touched_paths=[],
                dedupe_key="private",
            ),
            type(
                "Context",
                (),
                {
                    "preferred_event_types": ["push"],
                    "private_repo_mode": "conservative",
                    "blocked_terms": [],
                    "blocked_topics": [],
                    "ignored_paths": [],
                },
            )(),
            True,
            "draft_regeneration_private_disabled",
        ),
        (
            NormalizedEvent(
                event_type="push",
                repo_full_name="example/app",
                title="Secret rollout",
                description="Update",
                url="https://example.test",
                occurred_at="2026-07-22T00:00:00Z",
                sha=None,
                branch_name=None,
                touched_paths=[],
                dedupe_key="blocked",
            ),
            type(
                "Context",
                (),
                {
                    "preferred_event_types": ["push"],
                    "private_repo_mode": "normal",
                    "blocked_terms": ["secret"],
                    "blocked_topics": [],
                    "ignored_paths": [],
                },
            )(),
            False,
            "draft_regeneration_blocked",
        ),
    ],
)
def test_regeneration_rejects_disabled_or_unsafe_event_context(
    event: NormalizedEvent, context: object, repo_private: bool, code: str
) -> None:
    with pytest.raises(ConflictError) as error:
        _ensure_allowed(event, context, repo_private)

    assert error.value.code == code
