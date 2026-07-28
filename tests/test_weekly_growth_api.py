from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.db.base import Base
from app.db.outbox import OutboxEvent
from app.db.session import get_db
from app.main import create_app
from app.modules.drafts.models import drafts
from app.modules.identity.models.users import User
from app.modules.operator.models import (
    ActionRiskReview,
    ApprovalRequest,
    MarketingPlan,
    MeasurementWindow,
    OperatorRecommendation,
    OperatorRun,
    PlanAction,
    PreparedAsset,
)
from app.modules.operator.services.measurement_service import (
    MeasurementFollowupWorkflow,
    read_search_measurement,
)
from app.modules.operator.services.weekly_delivery_service import (
    WeeklyGrowthEmailWorkflow,
    build_weekly_email_projection,
)
from app.modules.operator.services.weekly_growth_service import request_manual_weekly_run
from app.modules.operator.services.x_asset_bridge_service import project_x_asset_to_draft
from app.modules.products.models import Product, product_repositories
from app.modules.repos.models import repos
from app.modules.search_intelligence.models import SearchDailyMetric
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import Workspace, WorkspaceMembership
from app.services.email.base import EmailMessage
from app.workflows.weekly_operator import (
    WeeklyOperatorScheduler,
    WeeklyOperatorTenantMismatchError,
    WeeklyOperatorWorkflow,
)

NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)
Phase5Client = tuple[TestClient, async_sessionmaker[AsyncSession]]


@pytest.fixture
def phase5_client() -> Generator[Phase5Client, None, None]:
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
    owner: User
    viewer: User
    outsider: User
    workspace: Workspace
    other_workspace: Workspace
    product: Product
    empty_product: Product
    sibling_product: Product
    run: OperatorRun
    plan: MarketingPlan
    recommendation: OperatorRecommendation
    action: PlanAction
    dismissible_action: PlanAction
    asset: PreparedAsset


async def seed_graph(sessions: async_sessionmaker[AsyncSession]) -> Graph:
    async with sessions() as db:
        owner = User(email=f"phase5-owner-{uuid4()}@example.com")
        viewer = User(email=f"phase5-viewer-{uuid4()}@example.com")
        outsider = User(email=f"phase5-outsider-{uuid4()}@example.com")
        workspace, other_workspace = Workspace(name="Phase 5"), Workspace(name="Other")
        db.add_all([owner, viewer, outsider, workspace, other_workspace])
        await db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=owner.id,
                    role=WorkspaceRole.owner,
                    is_active=True,
                ),
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=viewer.id,
                    role=WorkspaceRole.viewer,
                    is_active=True,
                ),
            ]
        )
        product = Product(
            workspace_id=workspace.id,
            user_id=owner.id,
            name="Target",
            weekly_growth_operator_enabled=True,
            operator_scheduled_enabled=True,
            operator_email_enabled=True,
            operator_weekday=0,
            operator_hour=9,
            operator_timezone="UTC",
        )
        empty_product = Product(workspace_id=workspace.id, user_id=owner.id, name="Empty")
        sibling_product = Product(workspace_id=workspace.id, user_id=owner.id, name="Sibling")
        db.add_all([product, empty_product, sibling_product])
        await db.flush()
        run = OperatorRun(
            workspace_id=workspace.id,
            product_id=product.id,
            run_kind="weekly_growth",
            trigger="scheduled",
            status="completed",
            idempotency_key=f"run-{uuid4()}",
            logical_period_start=NOW,
            logical_period_end=NOW + timedelta(days=7),
            workflow_version="v1",
            current_stage="ready",
            stage_summary={"sourceHealth": {"search": "healthy"}},
            analyzer_version="v1",
            provenance={},
            summary={},
            created_by_actor_id=owner.id,
            completed_at=NOW,
        )
        db.add(run)
        await db.flush()
        plan = MarketingPlan(
            workspace_id=workspace.id,
            product_id=product.id,
            operator_run_id=run.id,
            title="Weekly growth plan",
            status="ready",
            period_start=NOW,
            period_end=NOW + timedelta(days=7),
            idempotency_key=f"plan-{uuid4()}",
            provenance={},
            plan_revision=1,
            schema_version=1,
            selection_policy_version="v1",
            finalized_at=NOW,
            plan_snapshot={"shipped": {"count": 1}, "search": {"clicks": 10}},
            action_count=2,
            no_recommendation_count=0,
            created_by_actor_id=owner.id,
        )
        db.add(plan)
        await db.flush()
        recommendation = OperatorRecommendation(
            workspace_id=workspace.id,
            product_id=product.id,
            opportunity_id=uuid4(),
            operator_run_id=run.id,
            llm_execution_id=uuid4(),
            kind="recommendation",
            output={
                "title": "Publish update",
                "rationale": "A release shipped",
                "recommendation": "Share it",
                "confidence": "high",
                "evidenceIds": ["e-1"],
            },
            idempotency_key=f"recommendation-{uuid4()}",
        )
        db.add(recommendation)
        await db.flush()
        common: dict[str, Any] = {
            "workspace_id": workspace.id,
            "product_id": product.id,
            "plan_id": plan.id,
            "recommendation_id": recommendation.id,
            "title": "Publish update",
            "description": "Share the release",
            "evidence_ids": ["e-1"],
            "confidence": "high",
            "missing_information": [],
            "approval_level": "review",
            "approval_status": "pending",
            "cooldown_dedup_key": f"cooldown-{uuid4()}",
            "provenance": {},
            "prepared_output_type": "x_draft",
            "expected_metric": {},
            "measurement_window_days": 7,
            "estimated_effort": "low",
        }
        action = PlanAction(
            **common, position=1, status="pending", idempotency_key=f"action-{uuid4()}"
        )
        dismissible_action = PlanAction(
            **(common | {"cooldown_dedup_key": f"cooldown-{uuid4()}"}),
            position=2,
            status="pending",
            idempotency_key=f"action-{uuid4()}",
        )
        db.add_all([action, dismissible_action])
        await db.flush()
        asset = PreparedAsset(
            workspace_id=workspace.id,
            product_id=product.id,
            recommendation_id=recommendation.id,
            action_id=action.id,
            llm_execution_id=recommendation.llm_execution_id,
            revision=1,
            kind="x_draft",
            structured_content={
                "kind": "x_draft",
                "content": {"post": "Ship it"},
                "claimEvidence": [],
            },
        )
        db.add(asset)
        await db.flush()
        db.add_all(
            [
                ApprovalRequest(
                    workspace_id=workspace.id,
                    product_id=product.id,
                    action_id=action.id,
                    status="pending",
                    requested_by_actor_id=owner.id,
                    provenance={},
                ),
                ActionRiskReview(
                    workspace_id=workspace.id,
                    product_id=product.id,
                    asset_id=asset.id,
                    asset_revision=1,
                    llm_execution_id=recommendation.llm_execution_id,
                    deterministic_outcome="pass",
                    ai_outcome="pass",
                    final_outcome="pass",
                    findings=[],
                ),
            ]
        )
        await db.commit()
        return Graph(
            owner,
            viewer,
            outsider,
            workspace,
            other_workspace,
            product,
            empty_product,
            sibling_product,
            run,
            plan,
            recommendation,
            action,
            dismissible_action,
            asset,
        )


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def root(graph: Graph, product: Product | None = None, workspace: Workspace | None = None) -> str:
    return (
        f"/api/v1/workspaces/{(workspace or graph.workspace).id}/products/"
        f"{(product or graph.product).id}/operator"
    )


def mutation_headers(user: User, key: str) -> dict[str, str]:
    return auth(user) | {"Idempotency-Key": key}


def test_today_zero_current_ready_and_plan_reads(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    headers = auth(graph.viewer)
    zero = client.get(f"{root(graph, graph.empty_product)}/today", headers=headers)
    today = client.get(f"{root(graph)}/today", headers=headers)
    plans = client.get(f"{root(graph)}/plans", headers=headers)
    detail = client.get(f"{root(graph)}/plans/{graph.plan.id}", headers=headers)
    run_plan = client.get(f"{root(graph)}/runs/{graph.run.id}/plan", headers=headers)
    assert zero.status_code == 200 and zero.json()["plan"] is None and zero.json()["run"] is None
    assert today.status_code == 200 and today.json()["plan"]["id"] == str(graph.plan.id)
    assert today.json()["sourceHealth"] == {"search": "healthy"}
    assert plans.status_code == detail.status_code == run_plan.status_code == 200
    assert len(plans.json()) == 1 and len(detail.json()["actions"]) == 2


def test_recommendation_decisions_replay_and_scope(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    url = f"{root(graph)}/recommendations/{graph.recommendation.id}"
    assert client.get(url, headers=auth(graph.viewer)).status_code == 200
    first = client.post(f"{url}/accept", headers=mutation_headers(graph.owner, "accept"))
    replay = client.post(f"{url}/accept", headers=mutation_headers(graph.owner, "accept"))
    dismissed = client.post(
        f"{url}/dismiss",
        headers=mutation_headers(graph.owner, "dismiss"),
        json={"reason": "not_now"},
    )
    assert first.status_code == replay.status_code == dismissed.status_code == 200
    assert first.json()["acceptedAt"].removesuffix("Z") == replay.json()["acceptedAt"].removesuffix(
        "Z"
    )
    assert dismissed.json()["acceptedAt"] is None
    assert (
        client.get(
            url.replace(str(graph.product.id), str(graph.sibling_product.id)),
            headers=auth(graph.viewer),
        ).status_code
        == 404
    )
    assert (
        client.get(
            root(graph, workspace=graph.other_workspace)
            + f"/recommendations/{graph.recommendation.id}",
            headers=auth(graph.outsider),
        ).status_code
        == 404
    )


def test_asset_approval_edit_replay_conflict_and_rejection(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    action_url = f"{root(graph)}/actions/{graph.action.id}"
    asset_url = f"{root(graph)}/assets/{graph.asset.id}"
    headers = mutation_headers(graph.owner, "asset")
    approval = {"assetId": str(graph.asset.id), "revision": 1}
    assert client.get(action_url, headers=auth(graph.viewer)).status_code == 200
    assert len(client.get(f"{root(graph)}/actions", headers=auth(graph.viewer)).json()) == 2
    assert client.post(f"{action_url}/approve", headers=headers, json=approval).status_code == 200
    assert client.post(f"{action_url}/approve", headers=headers, json=approval).status_code == 200
    edit = client.patch(
        asset_url,
        headers=mutation_headers(graph.owner, "edit"),
        json={
            "revision": 1,
            "structuredContent": {"content": {"post": "Edited"}, "claim_evidence": []},
        },
    )
    replay = client.patch(
        asset_url,
        headers=mutation_headers(graph.owner, "edit"),
        json={
            "revision": 1,
            "structuredContent": {"content": {"post": "Edited"}, "claim_evidence": []},
        },
    )
    conflict = client.patch(
        asset_url,
        headers=mutation_headers(graph.owner, "changed"),
        json={
            "revision": 1,
            "structuredContent": {"content": {"post": "Different"}, "claim_evidence": []},
        },
    )
    assert edit.status_code == replay.status_code == 201
    assert edit.json()["id"] == replay.json()["id"] and edit.json()["revision"] == 2
    assert conflict.status_code == 409
    assert client.get(f"{asset_url}/revisions", headers=auth(graph.viewer)).status_code == 200
    rejected = client.post(
        f"{root(graph)}/assets/{edit.json()['id']}/reject",
        headers=mutation_headers(graph.owner, "reject"),
        json={"revision": 2, "reason": "Needs work"},
    )
    assert rejected.status_code == 200 and rejected.json()["approvalStatus"] == "rejected"


def test_action_transitions_complete_atomically_and_replay(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    action_url = f"{root(graph)}/actions/{graph.action.id}"
    headers = mutation_headers(graph.owner, "complete-flow")
    assert (
        client.post(
            f"{action_url}/approve",
            headers=headers,
            json={"assetId": str(graph.asset.id), "revision": 1},
        ).status_code
        == 200
    )
    assert client.post(f"{action_url}/start", headers=headers).status_code == 200
    completed = client.post(f"{action_url}/complete", headers=headers)
    replay = client.post(f"{action_url}/complete", headers=headers)
    measurement = client.get(f"{action_url}/measurement", headers=auth(graph.viewer))

    async def counts() -> tuple[int, int]:
        async with sessions() as db:
            windows = int(await db.scalar(select(func.count()).select_from(MeasurementWindow)) or 0)
            events = int(
                await db.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_name.like("operator/%measurement%"))
                )
                or 0
            )
            return windows, events

    assert completed.status_code == replay.status_code == measurement.status_code == 200
    assert completed.json()["status"] == "completed"
    assert client.portal.call(counts) == (1, 2)
    dismiss_url = f"{root(graph)}/actions/{graph.dismissible_action.id}/dismiss"
    assert (
        client.post(
            dismiss_url,
            headers=mutation_headers(graph.owner, "dismiss-action"),
            json={"reason": "not_now"},
        ).status_code
        == 200
    )


def test_mutations_require_write_role_and_hide_cross_product(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    denied = client.post(
        f"{root(graph)}/recommendations/{graph.recommendation.id}/accept",
        headers=mutation_headers(graph.viewer, "denied"),
    )
    hidden = client.get(
        f"{root(graph, graph.sibling_product)}/actions/{graph.action.id}",
        headers=auth(graph.viewer),
    )
    assert denied.status_code == 403
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_measurement_followup_handles_missing_manual_and_replay(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    async with sessions() as db:
        measurement = MeasurementWindow(
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            action_id=graph.action.id,
            status="scheduled",
            starts_at=NOW,
            ends_at=NOW + timedelta(days=7),
            due_at=NOW + timedelta(days=7),
            metric_contract={"metric": "revenue"},
            expected_metric={"metric": "revenue"},
            baseline={},
            baseline_period={},
            idempotency_key="manual-measurement",
            provenance={},
            limitations=[],
        )
        db.add(measurement)
        await db.commit()
        measurement_id = measurement.id
    workflow = MeasurementFollowupWorkflow(sessions=sessions)
    assert await workflow.process(uuid4(), graph.workspace.id, graph.product.id) == {
        "status": "not_found"
    }
    first = await workflow.process(measurement_id, graph.workspace.id, graph.product.id)
    replay = await workflow.process(measurement_id, graph.workspace.id, graph.product.id)
    assert first == {"status": "unavailable", "outcome": "unavailable", "duplicate": False}
    assert replay == {"status": "unavailable", "duplicate": True}


@pytest.mark.asyncio
async def test_search_measurement_aggregates_authoritative_daily_facts(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    query_id, page_id, source_id = uuid4(), uuid4(), uuid4()
    async with sessions() as db:
        db.add_all(
            SearchDailyMetric(
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                source_id=source_id,
                query_id=query_id,
                page_id=page_id,
                metric_date=NOW.date() + timedelta(days=offset),
                search_type="web",
                grain="query_page",
                clicks=clicks,
                impressions=impressions,
                average_position=Decimal(position),
            )
            for offset, clicks, impressions, position in [(-2, 2, 10, "4"), (-1, 3, 30, "2")]
        )
        await db.commit()
    contract: dict[str, object] = {
        "metric": "clicks",
        "queryId": str(query_id),
        "pageId": str(page_id),
    }
    async with sessions() as db:
        result = await read_search_measurement(
            db,
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            contract=contract,
            starts_on=NOW.date() - timedelta(days=3),
            ends_on=NOW.date(),
        )
        invalid = await read_search_measurement(
            db,
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            contract={"metric": "revenue"},
            starts_on=NOW.date() - timedelta(days=3),
            ends_on=NOW.date(),
        )
        measurement = MeasurementWindow(
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            action_id=graph.action.id,
            status="scheduled",
            starts_at=NOW - timedelta(days=3),
            ends_at=NOW,
            due_at=NOW,
            metric_contract=contract,
            expected_metric=contract,
            baseline={"clicks": 1},
            baseline_period={},
            baseline_value={"clicks": 1},
            idempotency_key="search-measurement",
            provenance={},
            limitations=[],
        )
        db.add(measurement)
        await db.commit()
        measurement_id = measurement.id
    assert result is not None
    assert result["clicks"] == 5 and result["impressions"] == 40
    assert result["ctr"] == "0.125" and Decimal(str(result["position"])) == Decimal("2.5")
    assert invalid is None
    followup = await MeasurementFollowupWorkflow(sessions=sessions).process(
        measurement_id, graph.workspace.id, graph.product.id
    )
    assert followup == {"status": "completed", "outcome": "improved", "duplicate": False}


class RecordingEmailService:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_weekly_email_projection_delivery_and_replay(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    async with sessions() as db:
        projection = await build_weekly_email_projection(
            db,
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            plan_id=graph.plan.id,
            frontend_url="https://app.example",
        )
    assert projection.subject == "Target: weekly growth plan"
    assert "Publish update" in projection.text
    email = RecordingEmailService()
    workflow = WeeklyGrowthEmailWorkflow(
        sessions=sessions, email_service=email, frontend_url="https://app.example"
    )
    first = await workflow.deliver(
        graph.plan.id, graph.workspace.id, graph.product.id, graph.plan.plan_revision or 1
    )
    replay = await workflow.deliver(
        graph.plan.id, graph.workspace.id, graph.product.id, graph.plan.plan_revision or 1
    )
    assert first["status"] == replay["status"] == "delivered"
    assert first["duplicate"] is False and replay["duplicate"] is True
    assert len(email.messages) == 1


@pytest.mark.asyncio
async def test_approved_x_asset_bridge_projects_one_publishable_draft(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    repo_id = uuid4()
    async with sessions() as db:
        await db.execute(
            insert(repos).values(
                id=repo_id,
                workspace_id=graph.workspace.id,
                user_id=graph.owner.id,
                repo_name="backend",
                repo_full_name="example/backend",
            )
        )
        await db.execute(
            insert(product_repositories).values(
                id=uuid4(),
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                repo_id=repo_id,
                repo_role="backend",
                is_primary_repo=True,
            )
        )
        first = await project_x_asset_to_draft(
            db,
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            asset_id=graph.asset.id,
            revision=1,
        )
        replay = await project_x_asset_to_draft(
            db,
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            asset_id=graph.asset.id,
            revision=1,
        )
        await db.commit()
        draft_count = await db.scalar(select(func.count()).select_from(drafts))
    assert first == replay and first is not None
    assert draft_count == 1


@pytest.mark.asyncio
async def test_scheduler_filters_products_and_replays_stable_run(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    async with sessions() as db:
        product = await db.get(Product, graph.product.id)
        assert product is not None
        product.operator_hour = 9
        product.operator_weekday = 0
        product.operator_timezone = "UTC"
        bad_zone = Product(
            workspace_id=graph.workspace.id,
            user_id=graph.owner.id,
            name="Bad zone",
            weekly_growth_operator_enabled=True,
            operator_scheduled_enabled=True,
            operator_weekday=0,
            operator_hour=9,
            operator_timezone="Not/AZone",
        )
        db.add(bad_zone)
        await db.commit()
    scheduler = WeeklyOperatorScheduler(sessions=sessions, now=lambda: NOW.replace(hour=9))
    first = await scheduler.schedule()
    replay = await scheduler.schedule()
    assert first == replay == {"scheduled": 1, "skipped": 1}
    async with sessions() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(OperatorRun)
            .where(OperatorRun.product_id == graph.product.id, OperatorRun.trigger == "scheduled")
        )
        assert count == 2  # Seeded ready run plus one stable scheduled run.


@pytest.mark.asyncio
async def test_manual_run_service_validates_and_commits_outbox(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    async with sessions() as db:
        with pytest.raises(ValueError, match="after"):
            await request_manual_weekly_run(
                db,
                workspace_id=graph.workspace.id,
                product_id=graph.empty_product.id,
                actor_id=graph.owner.id,
                period_start=NOW,
                period_end=NOW,
                workflow_version="v1",
                idempotency_key="invalid-period",
            )
        await db.rollback()
    async with sessions() as db:
        run = await request_manual_weekly_run(
            db,
            workspace_id=graph.workspace.id,
            product_id=graph.empty_product.id,
            actor_id=graph.owner.id,
            period_start=NOW,
            period_end=NOW + timedelta(days=7),
            workflow_version="v1",
            idempotency_key="manual-run",
        )
    async with sessions() as db:
        persisted = await db.get(OperatorRun, run.id)
        event_count = await db.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.aggregate_id == run.id)
        )
        assert persisted is not None and persisted.trigger == "manual"
        assert event_count == 1


class UnusedDependency:
    async def process(self, *args: object) -> dict[str, object]:
        raise AssertionError("dependency must not run")


class StoppedDetection:
    async def process(self, *args: object) -> dict[str, object]:
        return {"status": "stopped", "summary": {"reason": "no_sources"}}


@pytest.mark.asyncio
async def test_weekly_workflow_not_found_and_tenant_mismatch(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    dependency = UnusedDependency()
    workflow = WeeklyOperatorWorkflow(sessions=sessions, detection=dependency, ai=dependency)  # type: ignore[arg-type]
    missing = await workflow.process(uuid4(), graph.workspace.id, graph.product.id)
    assert missing["status"] == "not_found"
    with pytest.raises(WeeklyOperatorTenantMismatchError):
        await workflow.process(graph.run.id, graph.other_workspace.id, graph.product.id)


@pytest.mark.asyncio
async def test_weekly_workflow_ready_replay_and_stopped_detection(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    unused = UnusedDependency()
    replay_workflow = WeeklyOperatorWorkflow(
        sessions=sessions,
        detection=cast(Any, unused),
        ai=cast(Any, unused),
    )
    replay = await replay_workflow.process(graph.run.id, graph.workspace.id, graph.product.id)
    assert replay == {
        "status": "completed",
        "runId": str(graph.run.id),
        "planId": str(graph.plan.id),
    }
    async with sessions() as db:
        stopped_run = OperatorRun(
            workspace_id=graph.workspace.id,
            product_id=graph.empty_product.id,
            run_kind="weekly_growth",
            trigger="manual",
            status="pending",
            idempotency_key="stopped-weekly-run",
            logical_period_start=NOW,
            logical_period_end=NOW + timedelta(days=7),
            workflow_version="v1",
            current_stage="created",
            stage_summary={},
            analyzer_version="v1",
            provenance={},
            summary={},
            created_by_actor_id=graph.owner.id,
        )
        db.add(stopped_run)
        await db.commit()
        stopped_run_id = stopped_run.id
    stopped_workflow = WeeklyOperatorWorkflow(
        sessions=sessions,
        detection=cast(Any, StoppedDetection()),
        ai=cast(Any, unused),
    )
    stopped = await stopped_workflow.process(
        stopped_run_id, graph.workspace.id, graph.empty_product.id
    )
    assert stopped["status"] == "stopped"
    async with sessions() as db:
        persisted = await db.get(OperatorRun, stopped_run_id)
        assert persisted is not None and persisted.stopped_reason == "{'reason': 'no_sources'}"
