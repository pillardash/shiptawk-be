from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db.base import Base
from app.db.outbox import OutboxEvent
from app.db.session import get_db
from app.main import create_app
from app.modules.identity.models.users import User
from app.modules.operator.api.operator_run_router import (
    get_evaluation,
    get_run,
    list_evaluations,
    list_runs,
)
from app.modules.operator.api.opportunity_router import (
    get_evidence,
    get_evidence_detail,
    get_opportunity,
    list_opportunities,
)
from app.modules.operator.enums import DismissalReason, FeedbackReason
from app.modules.operator.models import (
    MarketingPlan,
    OperatorRun,
    Opportunity,
    OpportunityEvaluation,
    OpportunityEvidence,
    OpportunityFeedback,
    PlanAction,
)
from app.modules.operator.schemas import OpportunityFeedbackCommand
from app.modules.operator.services.operator_run_service import (
    ActiveOperatorRunConflictError,
    OperatorRunIdempotencyConflictError,
    OperatorRunRequest,
    ProductNotFoundError,
    request_operator_run,
)
from app.modules.operator.services.opportunity_feedback_service import (
    FeedbackIdempotencyConflictError,
    OpportunityNotFoundError,
    append_feedback,
    dismiss_opportunity,
    supersede_opportunity,
)
from app.modules.products.enums.product_profile_enum import ProductProfileStatus
from app.modules.products.models import Product, ProductProfile
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import Workspace, WorkspaceMembership

NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)
Phase4Client = tuple[Any, async_sessionmaker[AsyncSession]]


@pytest.fixture
def phase4_client() -> Generator[tuple[TestClient, async_sessionmaker[AsyncSession]], None, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        assert client.portal is not None
        client.portal.call(create_tables)
        yield client, sessions
        client.portal.call(engine.dispose)


@dataclass(frozen=True)
class Graph:
    users: dict[WorkspaceRole, User]
    outsider: User
    workspace: Workspace
    other_workspace: Workspace
    product: Product
    other_product: Product
    run: OperatorRun
    opportunity: Opportunity
    evaluation: OpportunityEvaluation
    evidence: OpportunityEvidence


async def seed_graph(sessions: async_sessionmaker[AsyncSession]) -> Graph:
    async with sessions() as db:
        users = {role: User(email=f"{role.value}-{uuid4()}@example.com") for role in WorkspaceRole}
        outsider = User(email=f"outside-{uuid4()}@example.com")
        workspace, other_workspace = Workspace(name="Phase 4"), Workspace(name="Other")
        db.add_all([*users.values(), outsider, workspace, other_workspace])
        await db.flush()
        db.add_all(
            WorkspaceMembership(
                workspace_id=workspace.id, user_id=user.id, role=role, is_active=True
            )
            for role, user in users.items()
        )
        owner = users[WorkspaceRole.owner]
        product = Product(workspace_id=workspace.id, user_id=owner.id, name="Target")
        other_product = Product(workspace_id=workspace.id, user_id=owner.id, name="Sibling")
        db.add_all([product, other_product])
        await db.flush()
        db.add(
            ProductProfile(
                workspace_id=workspace.id,
                product_id=product.id,
                status=ProductProfileStatus.approved,
                version=1,
                draft_revision=1,
                completeness_score=100,
                approved_at=NOW,
                approved_by=owner.id,
            )
        )
        run = OperatorRun(
            workspace_id=workspace.id,
            product_id=product.id,
            run_kind="opportunity_detection",
            trigger="manual",
            status="completed",
            idempotency_key=f"seed-{uuid4()}",
            request_fingerprint="r" * 64,
            analyzer_version="v1",
            provenance={"private": "x"},
            summary={"accepted": 1},
            created_by_actor_id=owner.id,
            as_of=NOW,
            completed_at=NOW,
        )
        db.add(run)
        await db.flush()
        opportunity = Opportunity(
            workspace_id=workspace.id,
            product_id=product.id,
            operator_run_id=run.id,
            opportunity_type="search_decline",
            action_family="seo_content_refresh",
            subject_kind="page",
            subject_keys={"url": "/safe"},
            identity_fingerprint="identity",
            observation_fingerprint="o" * 64,
            detector_id="decline",
            detector_version="v1",
            title="Refresh page",
            evidence_ids=[],
            interpretation="Traffic declined",
            recommendation="Refresh public copy",
            confidence="high",
            missing_information=[],
            approval_level="review",
            measurement_window="28_days",
            stop_conditions=[],
            impact_score=80,
            effort_score=20,
            urgency_score=70,
            confidence_score=90,
            strategic_fit_score=85,
            novelty_score=75,
            priority_score=88,
            expected_metric={"name": "clicks"},
            status="open",
            cooldown_dedup_key="identity",
            cooldown_until=NOW + timedelta(days=30),
            provenance={"privateRaw": "secret"},
            created_by_actor_id=owner.id,
            created_at=NOW,
        )
        db.add(opportunity)
        await db.flush()
        evaluation = OpportunityEvaluation(
            workspace_id=workspace.id,
            product_id=product.id,
            operator_run_id=run.id,
            accepted_opportunity_id=opportunity.id,
            detector_id="decline",
            detector_version="v1",
            outcome="accepted",
            evaluation_key="e" * 64,
            reason_codes=[],
            input_fingerprint="i" * 64,
            candidate_snapshot={"private": "candidate"},
            diagnostics={"private": "diagnostic"},
            input_schema_version="1",
            detector_suite_version="v1",
            quality_policy_version="v1",
            scoring_policy_version="v1",
            cooldown_policy_version="v1",
            score_version="v1",
            priority_score=88,
        )
        db.add(evaluation)
        await db.flush()
        evidence = OpportunityEvidence(
            workspace_id=workspace.id,
            product_id=product.id,
            evaluation_id=evaluation.id,
            opportunity_id=opportunity.id,
            evidence_key="public",
            source_type="search",
            source_record_type="metric",
            source_id=uuid4(),
            source_run_id=uuid4(),
            observed_at=NOW,
            period={"days": 28},
            metrics={"clicks": 10},
            public_safe_summary="Clicks declined",
            confidence_score=90,
            freshness_status="fresh",
            quality_warnings=[],
            source_fingerprint="s" * 64,
            schema_version="1",
        )
        db.add(evidence)
        await db.commit()
        return Graph(
            users,
            outsider,
            workspace,
            other_workspace,
            product,
            other_product,
            run,
            opportunity,
            evaluation,
            evidence,
        )


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def roots(graph: Graph) -> tuple[str, str]:
    base = f"/v1/workspaces/{graph.workspace.id}/products/{graph.product.id}"
    return f"{base}/operator/runs", f"{base}/opportunities"


@pytest.mark.parametrize("role", [WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor])
def test_write_roles_create_run(phase4_client: Phase4Client, role: WorkspaceRole) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    run_url, _ = roots(graph)
    response = client.post(
        run_url, headers={**auth(graph.users[role]), "Idempotency-Key": role.value}, json={}
    )
    assert response.status_code == 202


@pytest.mark.parametrize("role", [WorkspaceRole.reviewer, WorkspaceRole.viewer])
def test_read_only_roles_cannot_create_run(
    phase4_client: Phase4Client, role: WorkspaceRole
) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    response = client.post(
        roots(graph)[0], headers={**auth(graph.users[role]), "Idempotency-Key": role.value}, json={}
    )
    assert response.status_code == 403


def test_create_run_replays_and_emits_one_outbox_event(phase4_client: Phase4Client) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    url = roots(graph)[0]
    headers = {**auth(graph.users[WorkspaceRole.owner]), "Idempotency-Key": "stable"}
    first = client.post(url, headers=headers, json={})
    replay = client.post(url, headers=headers, json={})

    async def state() -> tuple[int, int]:
        async with sessions() as db:
            return (
                int(
                    await db.scalar(
                        select(func.count())
                        .select_from(OperatorRun)
                        .where(OperatorRun.idempotency_key == "stable")
                    )
                    or 0
                ),
                int(
                    await db.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(OutboxEvent.aggregate_id == UUID(first.json()["id"]))
                    )
                    or 0
                ),
            )

    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    assert client.portal.call(state) == (1, 1)


def test_active_competing_run_conflicts(phase4_client: Phase4Client) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    headers = auth(graph.users[WorkspaceRole.owner])
    first = client.post(roots(graph)[0], headers={**headers, "Idempotency-Key": "one"}, json={})
    second = client.post(roots(graph)[0], headers={**headers, "Idempotency-Key": "two"}, json={})
    assert first.status_code == 202
    assert second.status_code == 409


def test_run_key_body_conflict(phase4_client: Phase4Client) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    headers = {**auth(graph.users[WorkspaceRole.owner]), "Idempotency-Key": "body"}
    assert client.post(roots(graph)[0], headers=headers, json={}).status_code == 202
    assert (
        client.post(roots(graph)[0], headers=headers, json={"trigger": "scheduled"}).status_code
        == 422
    )


def test_run_list_detail_are_product_scoped_and_paginated(
    phase4_client: Phase4Client,
) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    run_url, _ = roots(graph)
    headers = auth(graph.users[WorkspaceRole.viewer])
    listing = client.get(run_url, params={"limit": 1, "offset": 0}, headers=headers)
    detail = client.get(f"{run_url}/{graph.run.id}", headers=headers)
    wrong = client.get(
        run_url.replace(str(graph.product.id), str(graph.other_product.id)) + f"/{graph.run.id}",
        headers=headers,
    )
    assert listing.status_code == detail.status_code == 200
    assert listing.json()["limit"] == len(listing.json()["items"]) == 1
    assert wrong.status_code == 404


def test_evaluation_filters_and_safe_serialization(phase4_client: Phase4Client) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    url = f"{roots(graph)[0]}/{graph.run.id}/evaluations"
    response = client.get(
        url,
        params={"outcome": "accepted", "detector": "decline"},
        headers=auth(graph.users[WorkspaceRole.viewer]),
    )
    body = response.json()[0]
    assert response.status_code == 200
    assert body["id"] == str(graph.evaluation.id)
    assert not ({"candidateSnapshot", "diagnostics", "inputFingerprint"} & body.keys())


def test_evaluation_detail_and_missing_are_scoped(phase4_client: Phase4Client) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    base = f"{roots(graph)[0]}/{graph.run.id}/evaluations"
    headers = auth(graph.users[WorkspaceRole.viewer])
    detail = client.get(f"{base}/{graph.evaluation.id}", headers=headers)
    missing = client.get(f"{base}/{uuid4()}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == str(graph.evaluation.id)
    assert missing.status_code == 404


def test_opportunity_filters_and_pagination(phase4_client: Phase4Client) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    response = client.get(
        roots(graph)[1],
        params={
            "status": "open",
            "type": "search_decline",
            "detector": "decline",
            "minPriority": 80,
            "limit": 1,
        },
        headers=auth(graph.users[WorkspaceRole.viewer]),
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(graph.opportunity.id)]
    assert response.json()["limit"] == 1


def test_opportunity_and_evidence_details_are_safe(phase4_client: Phase4Client) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    base = f"{roots(graph)[1]}/{graph.opportunity.id}"
    headers = auth(graph.users[WorkspaceRole.viewer])
    opportunity = client.get(base, headers=headers).json()
    evidence = client.get(f"{base}/evidence/{graph.evidence.id}", headers=headers).json()
    assert not ({"provenance", "createdByActorId", "cooldownDedupKey"} & opportunity.keys())
    assert evidence["publicSafeSummary"] == "Clicks declined"
    assert not ({"sourceId", "sourceRunId", "sourceFingerprint"} & evidence.keys())


def test_evidence_list_and_missing_detail_are_scoped(phase4_client: Phase4Client) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    base = f"{roots(graph)[1]}/{graph.opportunity.id}/evidence"
    headers = auth(graph.users[WorkspaceRole.viewer])
    listing = client.get(base, headers=headers)
    missing = client.get(f"{base}/{uuid4()}", headers=headers)
    wrong_product = client.get(
        base.replace(str(graph.product.id), str(graph.other_product.id)), headers=headers
    )
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [str(graph.evidence.id)]
    assert missing.status_code == wrong_product.status_code == 404


@pytest.mark.parametrize(
    "role", [WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor, WorkspaceRole.reviewer]
)
def test_dismiss_allowed_roles(phase4_client: Phase4Client, role: WorkspaceRole) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    response = client.post(
        f"{roots(graph)[1]}/{graph.opportunity.id}/dismiss",
        headers={**auth(graph.users[role]), "Idempotency-Key": role.value},
        json={"reason": "not_relevant"},
    )
    assert response.status_code == 200


def test_viewer_cannot_dismiss_and_invalid_reason_is_rejected(
    phase4_client: Phase4Client,
) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    url = f"{roots(graph)[1]}/{graph.opportunity.id}/dismiss"
    assert (
        client.post(
            url,
            headers={**auth(graph.users[WorkspaceRole.viewer]), "Idempotency-Key": "v"},
            json={"reason": "not_relevant"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            url,
            headers={**auth(graph.users[WorkspaceRole.owner]), "Idempotency-Key": "bad"},
            json={"reason": "invented"},
        ).status_code
        == 422
    )


def test_viewer_feedback_allowed_and_bounds_validated(phase4_client: Phase4Client) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    url = f"{roots(graph)[1]}/{graph.opportunity.id}/feedback"
    headers = {**auth(graph.users[WorkspaceRole.viewer]), "Idempotency-Key": "feedback"}
    assert (
        client.post(
            url, headers=headers, json={"reason": "useful", "usefulness": 5, "comment": "Good"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            url,
            headers={**headers, "Idempotency-Key": "low"},
            json={"reason": "useful", "usefulness": 0},
        ).status_code
        == 422
    )
    assert (
        client.post(
            url,
            headers={**headers, "Idempotency-Key": "long"},
            json={"reason": "useful", "comment": "x" * 501},
        ).status_code
        == 422
    )


@pytest.mark.parametrize("reason", list(FeedbackReason))
def test_every_feedback_reason_round_trips_with_release_eligibility(
    phase4_client: Phase4Client, reason: FeedbackReason
) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    response = client.post(
        f"{roots(graph)[1]}/{graph.opportunity.id}/feedback",
        headers={
            **auth(graph.users[WorkspaceRole.viewer]),
            "Idempotency-Key": f"taxonomy-feedback-{reason.value}",
        },
        json={"reason": reason.value, "evaluationEligible": False},
    )

    async def persisted() -> tuple[str, bool]:
        async with sessions() as db:
            row = await db.scalar(
                select(OpportunityFeedback).where(
                    OpportunityFeedback.id == UUID(response.json()["id"])
                )
            )
            assert row is not None
            return row.reason_code, row.evaluation_eligible

    assert response.status_code == 200
    assert (response.json()["reasonCode"], response.json()["evaluationEligible"]) == (
        reason.value,
        False,
    )
    assert client.portal.call(persisted) == (reason.value, False)


@pytest.mark.parametrize("reason", list(DismissalReason))
def test_every_dismissal_reason_round_trips(
    phase4_client: Phase4Client, reason: DismissalReason
) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    response = client.post(
        f"{roots(graph)[1]}/{graph.opportunity.id}/dismiss",
        headers={
            **auth(graph.users[WorkspaceRole.reviewer]),
            "Idempotency-Key": f"taxonomy-dismissal-{reason.value}",
        },
        json={"reason": reason.value, "evaluationEligible": False},
    )
    assert response.status_code == 200
    assert (response.json()["reasonCode"], response.json()["evaluationEligible"]) == (
        reason.value,
        False,
    )


@pytest.mark.parametrize("action,reason", [("feedback", "useful"), ("dismiss", "not_relevant")])
def test_feedback_mutations_replay_and_conflict(
    phase4_client: Phase4Client, action: str, reason: str
) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    url = f"{roots(graph)[1]}/{graph.opportunity.id}/{action}"
    headers = {**auth(graph.users[WorkspaceRole.owner]), "Idempotency-Key": f"same-{action}"}
    first = client.post(url, headers=headers, json={"reason": reason, "comment": "one"})
    replay = client.post(url, headers=headers, json={"reason": reason, "comment": "one"})
    conflict = client.post(url, headers=headers, json={"reason": reason, "comment": "two"})
    assert first.status_code == replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]
    assert conflict.status_code == 409


@pytest.mark.parametrize("family", ["run", "evaluation", "opportunity", "evidence"])
def test_wrong_workspace_resources_are_hidden(phase4_client: Phase4Client, family: str) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    base = f"/v1/workspaces/{graph.other_workspace.id}/products/{graph.product.id}"
    paths = {
        "run": f"{base}/operator/runs/{graph.run.id}",
        "evaluation": f"{base}/operator/runs/{graph.run.id}/evaluations/{graph.evaluation.id}",
        "opportunity": f"{base}/opportunities/{graph.opportunity.id}",
        "evidence": f"{base}/opportunities/{graph.opportunity.id}/evidence/{graph.evidence.id}",
    }
    assert client.get(paths[family], headers=auth(graph.outsider)).status_code == 404


@pytest.mark.parametrize("path", ["/v1/operator/runs", "/v1/opportunities"])
def test_old_routes_are_absent(phase4_client: Phase4Client, path: str) -> None:
    client, _ = phase4_client
    assert client.get(path).status_code == 404


def test_cookie_mutation_without_session_or_csrf_is_rejected(
    phase4_client: Phase4Client,
) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    response = client.post(roots(graph)[0], headers={"Idempotency-Key": "cookie"}, json={})
    assert response.status_code == 401


def test_service_validation_not_found_and_internal_supersede(
    phase4_client: Phase4Client,
) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)

    async def exercise() -> None:
        async with sessions() as db:
            with pytest.raises(ValueError, match="Idempotency-Key"):
                await request_operator_run(
                    db,
                    workspace_id=graph.workspace.id,
                    product_id=graph.product.id,
                    actor_id=graph.users[WorkspaceRole.owner].id,
                    idempotency_key=" ",
                    request=OperatorRunRequest(),
                )
            with pytest.raises(ProductNotFoundError):
                await request_operator_run(
                    db,
                    workspace_id=graph.workspace.id,
                    product_id=uuid4(),
                    actor_id=graph.users[WorkspaceRole.owner].id,
                    idempotency_key="missing-product",
                    request=OperatorRunRequest(),
                )
            command = OpportunityFeedbackCommand(opportunity_id=uuid4(), reason_code="useful")
            with pytest.raises(OpportunityNotFoundError):
                await append_feedback(
                    db,
                    workspace_id=graph.workspace.id,
                    product_id=graph.product.id,
                    actor_id=graph.users[WorkspaceRole.viewer].id,
                    idempotency_key="missing-opportunity",
                    command=command,
                )
            await supersede_opportunity(
                db,
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                opportunity_id=graph.opportunity.id,
                actor_id=graph.users[WorkspaceRole.owner].id,
            )

        async with sessions() as db:
            opportunity = await db.get(Opportunity, graph.opportunity.id)
            assert opportunity is not None
            assert opportunity.status == "superseded"

    client.portal.call(exercise)


def test_router_functions_cover_success_filters_and_missing_resources(
    phase4_client: Phase4Client,
) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    viewer = graph.users[WorkspaceRole.viewer]

    async def exercise() -> None:
        async with sessions() as db:
            runs = await list_runs(
                graph.workspace.id, graph.product.id, viewer, db, limit=10, offset=0
            )
            run = await get_run(graph.workspace.id, graph.product.id, graph.run.id, viewer, db)
            evaluations = await list_evaluations(
                graph.workspace.id,
                graph.product.id,
                graph.run.id,
                viewer,
                db,
                outcome="accepted",
                detector="decline",
                limit=10,
                offset=0,
            )
            evaluation = await get_evaluation(
                graph.workspace.id,
                graph.product.id,
                graph.run.id,
                graph.evaluation.id,
                viewer,
                db,
            )
            opportunities = await list_opportunities(
                graph.workspace.id,
                graph.product.id,
                viewer,
                db,
                status_filter="open",
                opportunity_type="search_decline",
                detector="decline",
                min_priority=80,
                limit=10,
                offset=0,
            )
            opportunity = await get_opportunity(
                graph.workspace.id, graph.product.id, graph.opportunity.id, viewer, db
            )
            evidence = await get_evidence(
                graph.workspace.id, graph.product.id, graph.opportunity.id, viewer, db
            )
            evidence_detail = await get_evidence_detail(
                graph.workspace.id,
                graph.product.id,
                graph.opportunity.id,
                graph.evidence.id,
                viewer,
                db,
            )
            assert runs.items[0].id == run.id == graph.run.id
            assert evaluations[0].id == evaluation.id == graph.evaluation.id
            assert opportunities.items[0].id == opportunity.id == graph.opportunity.id
            assert evidence[0].id == evidence_detail.id == graph.evidence.id

            with pytest.raises(Exception, match="Operator run not found"):
                await get_run(graph.workspace.id, graph.product.id, uuid4(), viewer, db)
            with pytest.raises(Exception, match="Evaluation not found"):
                await get_evaluation(
                    graph.workspace.id,
                    graph.product.id,
                    graph.run.id,
                    uuid4(),
                    viewer,
                    db,
                )
            with pytest.raises(Exception, match="Opportunity not found"):
                await get_opportunity(graph.workspace.id, graph.product.id, uuid4(), viewer, db)
            with pytest.raises(Exception, match="Evidence not found"):
                await get_evidence_detail(
                    graph.workspace.id,
                    graph.product.id,
                    graph.opportunity.id,
                    uuid4(),
                    viewer,
                    db,
                )

    client.portal.call(exercise)


def test_services_directly_cover_replay_conflict_and_validation(
    phase4_client: Phase4Client,
) -> None:
    client, sessions = phase4_client
    graph = client.portal.call(seed_graph, sessions)
    owner_id = graph.users[WorkspaceRole.owner].id

    async def exercise_runs() -> None:
        async with sessions() as db:
            first = await request_operator_run(
                db,
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                actor_id=owner_id,
                idempotency_key="direct-run",
                request=OperatorRunRequest(),
            )
        async with sessions() as db:
            replay = await request_operator_run(
                db,
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                actor_id=owner_id,
                idempotency_key="direct-run",
                request=OperatorRunRequest(),
            )
            assert replay.id == first.id
        async with sessions() as db:
            changed = OperatorRunRequest.model_construct(trigger=cast(Any, "scheduled"))
            with pytest.raises(OperatorRunIdempotencyConflictError):
                await request_operator_run(
                    db,
                    workspace_id=graph.workspace.id,
                    product_id=graph.product.id,
                    actor_id=owner_id,
                    idempotency_key="direct-run",
                    request=changed,
                )
        async with sessions() as db:
            with pytest.raises(ActiveOperatorRunConflictError):
                await request_operator_run(
                    db,
                    workspace_id=graph.workspace.id,
                    product_id=graph.product.id,
                    actor_id=owner_id,
                    idempotency_key="competing-direct-run",
                    request=OperatorRunRequest(),
                )

    async def exercise_feedback() -> None:
        command = OpportunityFeedbackCommand(
            opportunity_id=graph.opportunity.id,
            reason_code="useful",
            usefulness=4,
            comment="Direct",
        )
        async with sessions() as db:
            first = await append_feedback(
                db,
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                actor_id=owner_id,
                idempotency_key="direct-feedback",
                command=command,
            )
        async with sessions() as db:
            replay = await append_feedback(
                db,
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                actor_id=owner_id,
                idempotency_key="direct-feedback",
                command=command,
            )
            assert replay.id == first.id
        async with sessions() as db:
            changed = command.model_copy(update={"comment": "Changed"})
            with pytest.raises(FeedbackIdempotencyConflictError):
                await append_feedback(
                    db,
                    workspace_id=graph.workspace.id,
                    product_id=graph.product.id,
                    actor_id=owner_id,
                    idempotency_key="direct-feedback",
                    command=changed,
                )
        async with sessions() as db:
            invalid = OpportunityFeedbackCommand(
                opportunity_id=graph.opportunity.id, reason_code="useful"
            )
            with pytest.raises(ValueError, match="valid for dismissal"):
                await dismiss_opportunity(
                    db,
                    workspace_id=graph.workspace.id,
                    product_id=graph.product.id,
                    actor_id=owner_id,
                    idempotency_key="invalid-dismissal",
                    command=invalid,
                )

    client.portal.call(exercise_runs)
    client.portal.call(exercise_feedback)


def test_phase4_seed_has_no_legacy_plan_side_effects(phase4_client: Phase4Client) -> None:
    client, sessions = phase4_client
    client.portal.call(seed_graph, sessions)

    async def counts() -> tuple[int, int]:
        async with sessions() as db:
            return (
                int(await db.scalar(select(func.count()).select_from(MarketingPlan)) or 0),
                int(await db.scalar(select(func.count()).select_from(PlanAction)) or 0),
            )

    assert client.portal.call(counts) == (0, 0)
