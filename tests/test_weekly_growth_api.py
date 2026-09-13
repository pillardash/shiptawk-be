from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

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
from app.modules.integrations.models import IntegrationConnection
from app.modules.operator.api.operator_lookup_router import (
    get_action_by_id,
    get_asset_by_id,
    get_run_by_id,
    list_history,
)
from app.modules.operator.api.weekly_growth_router import (
    list_action_index,
    list_assets,
    list_measurements,
    list_plan_index,
)
from app.modules.operator.models import (
    ActionRiskReview,
    ApprovalRequest,
    MarketingPlan,
    MeasurementWindow,
    OperatorCommandReceipt,
    OperatorRecommendation,
    OperatorRun,
    PlanAction,
    PreparedAsset,
)
from app.modules.operator.services.measurement_service import (
    MeasurementFollowupWorkflow,
    read_search_measurement,
)
from app.modules.operator.services.plan_view_service import record_plan_view
from app.modules.operator.services.weekly_delivery_service import (
    WeeklyGrowthEmailWorkflow,
    build_weekly_email_projection,
)
from app.modules.operator.services.weekly_growth_service import request_manual_weekly_run
from app.modules.operator.services.x_asset_bridge_service import project_x_asset_to_draft
from app.modules.products.api.product_router import list_product_operator_summaries
from app.modules.products.enums.product_profile_enum import ProductProfileStatus
from app.modules.products.models import Product, ProductProfile, WebsiteSource, product_repositories
from app.modules.repos.models import repos
from app.modules.search_intelligence.models import SearchDailyMetric
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.models import Workspace, WorkspaceMembership
from app.services.email.base import EmailMessage, EmailSendOutcomeUnknown, EmailSendReceipt
from app.shared.exceptions import ConflictError
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
            search_mode="deferred_reduced",
            search_mode_decided_at=NOW,
            repository_evidence_mode="connected",
            repository_evidence_mode_decided_at=NOW,
        )
        empty_product = Product(workspace_id=workspace.id, user_id=owner.id, name="Empty")
        sibling_product = Product(workspace_id=workspace.id, user_id=owner.id, name="Sibling")
        db.add_all([product, empty_product, sibling_product])
        await db.flush()
        workspace.activation_product_id = product.id
        connection = IntegrationConnection(
            workspace_id=workspace.id,
            provider="github_app",
            external_account_id="123",
            status="active",
            idempotency_key=f"github:{workspace.id}",
        )
        db.add(connection)
        repo_id = uuid4()
        await db.execute(
            insert(repos).values(
                id=repo_id,
                workspace_id=workspace.id,
                user_id=owner.id,
                repo_name="target",
                repo_full_name="example/target",
                is_tracked=True,
            )
        )
        await db.execute(
            insert(product_repositories).values(
                id=uuid4(),
                workspace_id=workspace.id,
                product_id=product.id,
                repo_id=repo_id,
                repo_role="backend",
                is_primary_repo=False,
            )
        )
        db.add(
            WebsiteSource(
                workspace_id=workspace.id,
                product_id=product.id,
                base_url="https://example.com",
                normalized_base_url="https://example.com",
                status="active",
                last_successful_crawl_at=NOW,
                created_by=owner.id,
                updated_by=owner.id,
            )
        )
        db.add_all(
            ProductProfile(
                workspace_id=workspace.id,
                product_id=profile_product.id,
                status=ProductProfileStatus.approved,
                version=1,
                draft_revision=1,
                completeness_score=100,
                approved_at=NOW,
                approved_by=owner.id,
            )
            for profile_product in (product, empty_product)
        )
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
                "claim_evidence": [],
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
        f"/v1/workspaces/{(workspace or graph.workspace).id}/products/"
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
    assert today.json()["sourceHealth"] == {
        "github": "healthy",
        "website": "healthy",
        "searchConsole": "reduced",
        "observedAt": "2026-07-27T12:00:00",
    }
    assert plans.status_code == detail.status_code == run_plan.status_code == 200
    assert len(plans.json()) == 1 and len(detail.json()["actions"]) == 2


def test_activation_reduced_mode_finalize_replays_without_enabling_delivery(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)

    async def reset() -> None:
        async with sessions() as db:
            product = await db.get(Product, graph.product.id)
            workspace = await db.get(Workspace, graph.workspace.id)
            assert product is not None and workspace is not None
            product.search_mode = "required"
            product.search_mode_decided_at = None
            product.weekly_growth_operator_enabled = False
            product.operator_scheduled_enabled = False
            product.operator_email_enabled = False
            workspace.onboarding_completed = False
            await db.commit()

    client.portal.call(reset)
    base = f"/v1/workspaces/{graph.workspace.id}/activation"
    projection = client.get(base, headers=auth(graph.owner))
    deferred = client.put(
        f"{base}/search-mode",
        headers=mutation_headers(graph.owner, "search-mode"),
        json={"mode": "deferred_reduced"},
    )
    first = client.post(
        f"{base}/finalize-and-start",
        headers=mutation_headers(graph.owner, "activate"),
        json={},
    )
    replay = client.post(
        f"{base}/finalize-and-start",
        headers=mutation_headers(graph.owner, "activate"),
        json={},
    )
    payload_conflict = client.post(
        f"{base}/finalize-and-start",
        headers=mutation_headers(graph.owner, "activate"),
        json={"emailEnabled": True},
    )
    hidden = client.get(
        base.replace(str(graph.workspace.id), str(graph.other_workspace.id)),
        headers=auth(graph.outsider),
    )

    assert projection.status_code == 200
    assert projection.json()["activationProductId"] == str(graph.product.id)
    assert [stage["stage"] for stage in projection.json()["stages"]] == [
        "product",
        "evidence",
        "profile",
        "search",
        "first_run",
    ]
    assert deferred.status_code == 200
    assert deferred.json()["searchMode"] == "deferred_reduced"
    assert first.status_code == replay.status_code == 202
    assert first.json()["run"]["id"] == replay.json()["run"]["id"]
    assert replay.json()["replayed"] is True
    assert payload_conflict.status_code == 409
    assert payload_conflict.json()["code"] == "idempotency_payload_conflict"
    assert first.json()["canonicalDestination"] == f"/users/today?productId={graph.product.id}"
    assert first.json()["activation"]["onboardingComplete"] is True
    assert first.json()["activation"]["nextSchedule"] is None
    assert hidden.status_code == 404

    async def settings() -> tuple[bool, bool, bool, str]:
        async with sessions() as db:
            product = await db.get(Product, graph.product.id)
            assert product is not None
            return (
                product.operator_scheduled_enabled,
                product.operator_email_enabled,
                product.operator_manual_enabled,
                product.search_mode,
            )

    assert client.portal.call(settings) == (False, False, True, "deferred_reduced")


def test_activation_projection_and_commands_handle_no_selected_product(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)

    async def clear_selection() -> None:
        async with sessions() as db:
            workspace = await db.get(Workspace, graph.workspace.id)
            assert workspace is not None
            workspace.activation_product_id = None
            workspace.onboarding_completed = False
            await db.commit()

    client.portal.call(clear_selection)
    base = f"/v1/workspaces/{graph.workspace.id}/activation"
    projection = client.get(base, headers=auth(graph.owner))
    finalize = client.post(
        f"{base}/finalize-and-start",
        headers=mutation_headers(graph.owner, "no-product-finalize"),
        json={},
    )
    search = client.put(
        f"{base}/search-mode",
        headers=mutation_headers(graph.owner, "no-product-search"),
        json={"mode": "required"},
    )
    missing = client.put(
        f"{base}/product",
        headers=mutation_headers(graph.owner, "missing-product"),
        json={"productId": str(uuid4())},
    )
    assert projection.status_code == 200
    assert projection.json()["activationProductId"] is None
    assert projection.json()["stages"][0]["blockers"] == ["activation_product_required"]
    assert projection.json()["canonicalDestination"] == "/users/products"
    assert finalize.status_code == 409
    assert finalize.json()["code"] == "activation_product_required"
    assert search.status_code == 409
    assert search.json()["code"] == "activation_product_required"
    assert missing.status_code == 404


def test_activation_finalize_returns_all_source_blockers_for_incomplete_product(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)

    async def select_incomplete() -> None:
        async with sessions() as db:
            workspace = await db.get(Workspace, graph.workspace.id)
            assert workspace is not None
            workspace.activation_product_id = graph.sibling_product.id
            workspace.onboarding_completed = False
            await db.commit()

    client.portal.call(select_incomplete)
    response = client.post(
        f"/v1/workspaces/{graph.workspace.id}/activation/finalize-and-start",
        headers=mutation_headers(graph.owner, "incomplete-finalize"),
        json={},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "repository_evidence_decision_required"
    assert response.json()["metadata"]["blockers"] == [
        "repository_evidence_decision_required",
        "approved_product_profile_required",
        "successful_website_crawl_required",
        "search_connection_or_explicit_reduced_mode_required",
    ]


def test_activation_can_persist_deferred_repository_evidence_without_github_recovery(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)

    async def select_product_without_repositories() -> None:
        async with sessions() as db:
            workspace = await db.get(Workspace, graph.workspace.id)
            product = await db.get(Product, graph.sibling_product.id)
            assert workspace is not None and product is not None
            workspace.activation_product_id = product.id
            workspace.onboarding_completed = False
            await db.commit()

    client.portal.call(select_product_without_repositories)
    url = f"/v1/workspaces/{graph.workspace.id}/activation/repository-evidence-mode"
    first = client.put(
        url,
        headers=mutation_headers(graph.owner, "defer-repositories"),
        json={"mode": "deferred"},
    )
    replay = client.put(
        url,
        headers=mutation_headers(graph.owner, "defer-repositories"),
        json={"mode": "deferred"},
    )
    hidden = client.put(
        url.replace(str(graph.workspace.id), str(graph.other_workspace.id)),
        headers=mutation_headers(graph.outsider, "cross-tenant-defer"),
        json={"mode": "deferred"},
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["repositoryEvidenceDecision"] == "deferred"
    assert first.json()["readiness"]["evidenceMode"] == "website_search"
    assert first.json()["readiness"]["optionalEnhancements"] == ["Add shipping evidence"]
    assert "github_installation_required" not in first.json()["readiness"]["requiredBlockers"]
    assert hidden.status_code == 404


def test_activation_selection_and_search_commands_are_authoritative_and_idempotent(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    base = f"/v1/workspaces/{graph.workspace.id}/activation"

    async def unlock() -> None:
        async with sessions() as db:
            workspace = await db.get(Workspace, graph.workspace.id)
            assert workspace is not None
            workspace.onboarding_completed = False
            await db.commit()

    client.portal.call(unlock)
    sibling = client.put(
        f"{base}/product",
        headers=mutation_headers(graph.owner, "select-product"),
        json={"productId": str(graph.sibling_product.id)},
    )
    sibling_replay = client.put(
        f"{base}/product",
        headers=mutation_headers(graph.owner, "select-product"),
        json={"productId": str(graph.sibling_product.id)},
    )
    selection_conflict = client.put(
        f"{base}/product",
        headers=mutation_headers(graph.owner, "select-product"),
        json={"productId": str(graph.product.id)},
    )
    selected = client.put(
        f"{base}/product",
        headers=mutation_headers(graph.owner, "select-target"),
        json={"productId": str(graph.product.id)},
    )
    missing = client.put(
        f"{base}/product",
        headers=mutation_headers(graph.owner, "select-missing"),
        json={"productId": str(uuid4())},
    )
    connected_without_baseline = client.put(
        f"{base}/search-mode",
        headers=mutation_headers(graph.owner, "search-connected"),
        json={"mode": "connected"},
    )
    required = client.put(
        f"{base}/search-mode",
        headers=mutation_headers(graph.owner, "search-required"),
        json={"mode": "required"},
    )
    search_conflict = client.put(
        f"{base}/search-mode",
        headers=mutation_headers(graph.owner, "search-required"),
        json={"mode": "deferred_reduced"},
    )

    assert sibling.status_code == sibling_replay.status_code == 200
    assert sibling.json()["activationProductId"] == str(graph.sibling_product.id)
    assert selection_conflict.status_code == 409
    assert selection_conflict.json()["code"] == "idempotency_payload_conflict"
    assert selected.status_code == 200
    assert missing.status_code == 404
    assert connected_without_baseline.status_code == 409
    assert connected_without_baseline.json()["code"] == "search_baseline_required"
    assert required.status_code == 200 and required.json()["searchMode"] == "required"
    assert search_conflict.status_code == 409
    assert search_conflict.json()["code"] == "idempotency_payload_conflict"

    async def lock() -> None:
        async with sessions() as db:
            workspace = await db.get(Workspace, graph.workspace.id)
            assert workspace is not None
            workspace.onboarding_completed = True
            await db.commit()

    client.portal.call(lock)
    locked = client.put(
        f"{base}/product",
        headers=mutation_headers(graph.owner, "locked-selection"),
        json={"productId": str(graph.sibling_product.id)},
    )
    assert locked.status_code == 409
    assert locked.json()["code"] == "activation_product_locked"


def test_activation_finalize_applies_only_explicit_schedule_and_email_flags(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)

    async def reset() -> UUID:
        async with sessions() as db:
            workspace = await db.get(Workspace, graph.workspace.id)
            product = await db.get(Product, graph.product.id)
            assert workspace is not None and product is not None
            workspace.onboarding_completed = False
            product.weekly_growth_operator_enabled = False
            product.operator_scheduled_enabled = False
            product.operator_email_enabled = False
            active = OperatorRun(
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                run_kind="weekly_growth",
                trigger="manual",
                status="pending",
                idempotency_key=f"active-before-finalize-{uuid4()}",
                logical_period_start=NOW + timedelta(days=28),
                logical_period_end=NOW + timedelta(days=35),
                workflow_version="active-before-finalize",
                current_stage="created",
                analyzer_version="v1",
                provenance={},
                summary={},
                created_by_actor_id=graph.owner.id,
            )
            db.add(active)
            await db.commit()
            return active.id

    active_id = client.portal.call(reset)
    response = client.post(
        f"/v1/workspaces/{graph.workspace.id}/activation/finalize-and-start",
        headers=mutation_headers(graph.owner, "explicit-delivery"),
        json={
            "scheduledEnabled": True,
            "emailEnabled": True,
            "timezone": "America/New_York",
            "weekday": 2,
            "hour": 11,
        },
    )
    assert response.status_code == 202
    assert response.json()["run"]["id"] == str(active_id)
    assert response.json()["activation"]["nextSchedule"] is not None

    async def settings() -> tuple[bool, bool, str, int, int]:
        async with sessions() as db:
            product = await db.get(Product, graph.product.id)
            assert product is not None
            return (
                product.operator_scheduled_enabled,
                product.operator_email_enabled,
                product.operator_timezone,
                product.operator_weekday,
                product.operator_hour,
            )

    assert client.portal.call(settings) == (True, True, "America/New_York", 2, 11)


def test_today_keeps_previous_report_when_new_run_is_pending_or_failed(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)

    async def add_run(status: str) -> UUID:
        async with sessions() as db:
            run = OperatorRun(
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                run_kind="weekly_growth",
                trigger="manual",
                status=status,
                idempotency_key=f"continuity-{status}-{uuid4()}",
                logical_period_start=NOW + timedelta(days=7 if status == "pending" else 14),
                logical_period_end=NOW + timedelta(days=14 if status == "pending" else 21),
                workflow_version=f"continuity-{status}",
                current_stage="created",
                analyzer_version="v1",
                provenance={},
                summary={},
                created_by_actor_id=graph.owner.id,
                started_at=datetime.now(UTC) + timedelta(minutes=1),
            )
            db.add(run)
            await db.commit()
            return run.id

    pending_id = client.portal.call(add_run, "pending")
    pending = client.get(f"{root(graph)}/today", headers=auth(graph.viewer)).json()
    assert pending["currentRun"]["id"] == str(pending_id)
    assert pending["latestFinalizedReport"]["id"] == str(graph.plan.id)

    async def fail_pending() -> None:
        async with sessions() as db:
            run = await db.get(OperatorRun, pending_id)
            assert run is not None
            run.status = "failed"
            run.failure_category = "provider_error"
            await db.commit()

    client.portal.call(fail_pending)
    failed = client.get(f"{root(graph)}/today", headers=auth(graph.viewer)).json()
    assert failed["currentRun"]["status"] == "failed"
    assert failed["latestFinalizedReport"]["id"] == str(graph.plan.id)


def test_operator_readiness_config_and_product_summaries(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    headers = auth(graph.viewer)

    readiness = client.get(f"{root(graph)}/readiness", headers=headers)
    summaries = client.get(
        f"/v1/workspaces/{graph.workspace.id}/products/operator-summaries",
        headers=headers,
    )
    denied = client.patch(
        f"{root(graph)}/config",
        headers=auth(graph.viewer),
        json={"emailEnabled": False},
    )
    updated = client.patch(
        f"{root(graph)}/config",
        headers=auth(graph.owner),
        json={
            "enabled": True,
            "manualEnabled": True,
            "scheduledEnabled": False,
            "emailEnabled": False,
            "timezone": "Europe/London",
            "weekday": 4,
            "hour": 15,
        },
    )

    assert readiness.status_code == 200
    assert readiness.json()["searchMode"] == "deferred_reduced"
    assert denied.status_code == 403
    assert updated.status_code == 200

    async def config() -> tuple[bool, bool, str, int, int]:
        async with sessions() as db:
            product = await db.get(Product, graph.product.id)
            assert product is not None
            return (
                product.operator_scheduled_enabled,
                product.operator_email_enabled,
                product.operator_timezone,
                product.operator_weekday,
                product.operator_hour,
            )

    assert client.portal.call(config) == (False, False, "Europe/London", 4, 15)

    assert summaries.status_code == 200
    items = {item["productId"]: item for item in summaries.json()["items"]}
    target = items[str(graph.product.id)]
    empty = items[str(graph.empty_product.id)]
    assert target["latestRunId"] == str(graph.run.id)
    assert target["latestPlanId"] == str(graph.plan.id)
    assert target["openActionCount"] == 2
    assert target["awaitingApprovalCount"] == 2
    assert target["nextSetupAction"] is None
    assert empty["ready"] is False
    assert empty["blockers"] == ["activation_product_mismatch"]


def test_phase5_collection_filters_pagination_and_history(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)

    async def add_measurements() -> None:
        async with sessions() as db:
            other_product = Product(
                workspace_id=graph.other_workspace.id,
                user_id=graph.outsider.id,
                name="Other tenant product",
            )
            db.add(other_product)
            await db.flush()
            db.add_all(
                [
                    MeasurementWindow(
                        workspace_id=graph.workspace.id,
                        product_id=graph.product.id,
                        action_id=graph.action.id,
                        status=status,
                        starts_at=NOW,
                        ends_at=NOW + timedelta(days=7),
                        due_at=NOW + timedelta(days=7),
                        metric_contract={"metric": "clicks"},
                        expected_metric={"metric": "clicks"},
                        baseline={"clicks": 1},
                        result={"clicks": 2} if status == "completed" else None,
                        outcome="improved" if status == "completed" else None,
                        idempotency_key=f"collection-{status}",
                        provenance={},
                        limitations=["partial window"] if status == "completed" else [],
                    )
                    for status in ("scheduled", "completed")
                ]
                + [
                    OperatorRun(
                        workspace_id=graph.other_workspace.id,
                        product_id=other_product.id,
                        run_kind="weekly_growth",
                        trigger="scheduled",
                        status="pending",
                        idempotency_key=f"other-run-{uuid4()}",
                        workflow_version="v1",
                        analyzer_version="v1",
                        provenance={},
                        summary={},
                        created_by_actor_id=graph.outsider.id,
                    )
                ]
            )
            await db.commit()

    client.portal.call(add_measurements)
    headers = auth(graph.viewer)
    plan_page = client.get(f"{root(graph)}/plan-index?limit=1&offset=0", headers=headers)
    actions = client.get(
        f"{root(graph)}/action-index?action_status=pending&limit=1&offset=0", headers=headers
    )
    measurements = client.get(
        f"{root(graph)}/measurement-index?measurement_status=completed&limit=1&offset=0",
        headers=headers,
    )
    assets = client.get(f"{root(graph)}/assets?kind=x_draft&limit=1&offset=0", headers=headers)
    history = client.get(
        f"/v1/workspaces/{graph.workspace.id}/operator/history"
        f"?product_id={graph.product.id}&entry_type=action&limit=1&offset=0",
        headers=headers,
    )
    history_second = client.get(
        f"/v1/workspaces/{graph.workspace.id}/operator/history"
        f"?product_id={graph.product.id}&entry_type=action&limit=1&offset=1",
        headers=headers,
    )
    pending_history = client.get(
        f"/v1/workspaces/{graph.workspace.id}/operator/history?status=pending",
        headers=headers,
    )
    completed_history = client.get(
        f"/v1/workspaces/{graph.workspace.id}/operator/history?status=completed",
        headers=headers,
    )

    assert plan_page.status_code == actions.status_code == measurements.status_code == 200
    assert assets.status_code == history.status_code == history_second.status_code == 200
    assert pending_history.status_code == completed_history.status_code == 200
    assert plan_page.json()["page"] == {"limit": 1, "offset": 0, "total": 1, "hasNext": False}
    assert actions.json()["page"]["total"] == 2
    assert actions.json()["page"]["hasNext"] is True
    assert actions.json()["items"][0]["status"] == "pending"
    assert measurements.json()["page"]["total"] == 1
    assert measurements.json()["items"][0]["outcome"] == "improved"
    assert measurements.json()["items"][0]["limitations"] == ["partial window"]
    assert assets.json()["page"]["total"] == 1
    assert assets.json()["items"][0]["kind"] == "x_draft"
    assert history.json()["page"]["total"] == 2
    assert history.json()["page"]["hasNext"] is True
    assert history.json()["items"][0]["entryType"] == "action"
    assert history_second.json()["page"]["hasNext"] is False
    assert history_second.json()["items"][0]["id"] != history.json()["items"][0]["id"]
    assert pending_history.json()["page"]["total"] == 2
    assert {item["entryType"] for item in pending_history.json()["items"]} == {"action"}
    assert completed_history.json()["page"]["total"] == 2
    assert {item["entryType"] for item in completed_history.json()["items"]} == {
        "run",
        "measurement",
    }


def test_canonical_operator_lookups_return_typed_not_found_errors(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    headers = auth(graph.viewer)
    missing_id = uuid4()
    canonical = f"/v1/workspaces/{graph.workspace.id}/operator"

    responses = [
        client.get(f"{canonical}/runs/{missing_id}", headers=headers),
        client.get(f"{canonical}/actions/{missing_id}", headers=headers),
        client.get(f"{canonical}/assets/{missing_id}", headers=headers),
        client.get(f"{root(graph)}/plans/{missing_id}", headers=headers),
        client.get(f"{root(graph)}/runs/{missing_id}/plan", headers=headers),
        client.get(f"{root(graph)}/recommendations/{missing_id}", headers=headers),
        client.get(f"{root(graph)}/actions/{missing_id}", headers=headers),
        client.get(f"{root(graph)}/assets/{missing_id}", headers=headers),
    ]

    assert [response.status_code for response in responses] == [404] * len(responses)
    assert [response.json()["code"] for response in responses] == [
        "operator_run_not_found",
        "action_not_found",
        "asset_not_found",
        "plan_not_found",
        "plan_not_found",
        "recommendation_not_found",
        "action_not_found",
        "asset_not_found",
    ]


@pytest.mark.asyncio
async def test_phase5_public_read_functions_query_persisted_collections(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    async with sessions() as db:
        db.add(
            MeasurementWindow(
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                action_id=graph.action.id,
                status="completed",
                starts_at=NOW,
                ends_at=NOW + timedelta(days=7),
                due_at=NOW + timedelta(days=7),
                metric_contract={"metric": "clicks"},
                expected_metric={"metric": "clicks"},
                baseline={"clicks": 2},
                result={"clicks": 4},
                outcome="improved",
                idempotency_key="direct-public-read",
                provenance={},
                limitations=[],
            )
        )
        await db.commit()

        plans = await list_plan_index(
            graph.workspace.id, graph.product.id, graph.viewer, db, limit=1, offset=0
        )
        actions = await list_action_index(
            graph.workspace.id,
            graph.product.id,
            graph.viewer,
            db,
            action_status=None,
            limit=1,
            offset=1,
        )
        assets = await list_assets(
            graph.workspace.id,
            graph.product.id,
            graph.viewer,
            db,
            kind=None,
            approval_status=None,
            limit=1,
            offset=0,
        )
        measurements = await list_measurements(
            graph.workspace.id,
            graph.product.id,
            graph.viewer,
            db,
            measurement_status="completed",
            limit=1,
            offset=0,
        )
        summaries = await list_product_operator_summaries(graph.workspace.id, graph.viewer, db)
        run = await get_run_by_id(graph.workspace.id, graph.run.id, graph.viewer, db)
        action = await get_action_by_id(graph.workspace.id, graph.action.id, graph.viewer, db)
        asset = await get_asset_by_id(graph.workspace.id, graph.asset.id, graph.viewer, db)
        history = await list_history(
            graph.workspace.id,
            graph.viewer,
            db,
            product_id=graph.product.id,
            entry_type="measurement",
            status=None,
            limit=1,
            offset=0,
        )

    assert plans.page.total == 1
    assert actions.page.total == 2 and actions.page.offset == 1
    assert assets.page.total == 1 and assets.items[0].id == graph.asset.id
    assert measurements.items[0].result == {"clicks": 4}
    assert len(summaries.items) == 3
    assert run.id == graph.run.id and len(run.actions) == 2
    assert run.plan_id == graph.plan.id and run.plan_revision == 1
    assert action.action.id == graph.action.id
    assert action.prepared_output is not None
    assert action.measurement is not None and action.measurement.outcome == "improved"
    assert asset.id == graph.asset.id
    assert history.page.total == 1
    assert history.items[0].entry_type == "measurement"
    assert history.items[0].action_id == graph.action.id
    assert history.items[0].canonical_destination == f"/users/history/actions/{graph.action.id}"


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
        json={"reason": "not_now", "comment": "Revisit next quarter"},
    )
    assert first.status_code == replay.status_code == 200
    assert dismissed.status_code == 409
    assert first.json()["acceptedAt"].removesuffix("Z") == replay.json()["acceptedAt"].removesuffix(
        "Z"
    )
    assert dismissed.json()["code"] == "invalid_recommendation_transition"
    assert (
        client.get(
            url.replace(str(graph.product.id), str(graph.sibling_product.id)),
            headers=auth(graph.viewer),
        ).status_code
        == 404
    )


def test_workspace_canonical_history_lookups_are_tenant_safe(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    base = f"/v1/workspaces/{graph.workspace.id}/operator"
    headers = auth(graph.viewer)

    run = client.get(f"{base}/runs/{graph.run.id}", headers=headers)
    action = client.get(f"{base}/actions/{graph.action.id}", headers=headers)
    asset = client.get(f"{base}/assets/{graph.asset.id}", headers=headers)

    assert run.status_code == action.status_code == asset.status_code == 200
    assert run.json()["productId"] == str(graph.product.id)
    assert action.json()["action"]["productId"] == str(graph.product.id)
    assert asset.json()["productId"] == str(graph.product.id)
    assert asset.json()["isCurrent"] is True
    assert asset.json()["currentRevision"] == 1
    assert action.json()["preparedOutput"]["id"] == str(graph.asset.id)

    hidden = f"/v1/workspaces/{graph.other_workspace.id}/operator"
    assert (
        client.get(f"{hidden}/runs/{graph.run.id}", headers=auth(graph.outsider)).status_code == 404
    )
    assert (
        client.get(f"{hidden}/actions/{graph.action.id}", headers=auth(graph.outsider)).status_code
        == 404
    )
    assert (
        client.get(f"{hidden}/assets/{graph.asset.id}", headers=auth(graph.outsider)).status_code
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


@pytest.mark.asyncio
async def test_report_view_ordinal_counts_logical_periods_not_revisions(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    async with sessions() as db:
        first, replayed = await record_plan_view(
            db,
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            actor_id=graph.viewer.id,
            plan=graph.plan,
        )
    async with sessions() as db:
        revision = MarketingPlan(
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            operator_run_id=graph.run.id,
            title="Revised weekly growth plan",
            status="ready",
            period_start=graph.plan.period_start,
            period_end=graph.plan.period_end,
            idempotency_key=f"plan-revision-{uuid4()}",
            provenance={},
            plan_revision=2,
            schema_version=1,
            selection_policy_version="v1",
            finalized_at=NOW + timedelta(hours=1),
            plan_snapshot={},
            action_count=0,
            no_recommendation_count=1,
            created_by_actor_id=graph.owner.id,
        )
        db.add(revision)
        await db.commit()
        same_period, same_period_replayed = await record_plan_view(
            db,
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            actor_id=graph.viewer.id,
            plan=revision,
        )
    assert replayed is False
    assert same_period_replayed is True
    assert first.id == same_period.id
    assert same_period.report_ordinal == 1


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
    metadata = conflict.json()["metadata"]
    assert metadata["currentRevision"] == 2
    assert metadata["currentAssetId"] == edit.json()["id"]
    assert metadata["safeRecovery"] == "compare_or_reload"
    key_conflict = client.patch(
        asset_url,
        headers=mutation_headers(graph.owner, "edit"),
        json={
            "revision": 1,
            "structuredContent": {"content": {"post": "Different"}, "claim_evidence": []},
        },
    )
    assert key_conflict.status_code == 409
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
    assert client.portal.call(counts) == (1, 1)
    dismiss_url = f"{root(graph)}/actions/{graph.dismissible_action.id}/dismiss"
    assert (
        client.post(
            dismiss_url,
            headers=mutation_headers(graph.owner, "dismiss-action"),
            json={"reason": "not_now", "comment": "Focus elsewhere"},
        ).status_code
        == 200
    )

    async def dismissal() -> tuple[str | None, str | None, int]:
        async with sessions() as db:
            row = await db.get(PlanAction, graph.dismissible_action.id)
            assert row is not None
            receipts = int(
                await db.scalar(select(func.count()).select_from(OperatorCommandReceipt)) or 0
            )
            return row.dismissal_reason, row.dismissal_comment, receipts

    assert client.portal.call(dismissal) == ("not_now", "Focus elsewhere", 4)
    projected = client.get(
        f"{root(graph)}/actions/{graph.dismissible_action.id}", headers=auth(graph.viewer)
    ).json()
    assert projected["dismissalReason"] == "not_now"
    assert projected["dismissalComment"] == "Focus elsewhere"
    assert projected["dismissedByActorId"] == str(graph.owner.id)


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

    def send(self, message: EmailMessage) -> EmailSendReceipt:
        self.messages.append(message)
        return EmailSendReceipt("provider-message-id", (message.to[0].email,))


@pytest.mark.asyncio
async def test_weekly_email_projection_delivery_and_replay(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    async with sessions() as db:
        current_asset = PreparedAsset(
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            recommendation_id=graph.asset.recommendation_id,
            action_id=graph.action.id,
            llm_execution_id=graph.asset.llm_execution_id,
            revision=2,
            kind="x_draft",
            structured_content={
                "kind": "x_draft",
                "content": {"post": "Ship the revised version"},
                "claim_evidence": [],
            },
        )
        db.add(current_asset)
        await db.commit()
        projection = await build_weekly_email_projection(
            db,
            workspace_id=graph.workspace.id,
            product_id=graph.product.id,
            plan_id=graph.plan.id,
            frontend_url="https://app.example",
        )
    assert projection.subject == "Target: weekly growth plan"
    assert "Publish update" in projection.text
    assert f"https://app.example/history/runs/{graph.run.id}" in projection.text
    assert f"https://app.example/history/actions/{graph.action.id}" in projection.text
    assert f"https://app.example/content/{current_asset.id}?revision=2" in projection.text
    assert f"https://app.example/content/{graph.asset.id}?revision=1" not in projection.text
    assert "<pre>" not in projection.html
    assert "#45B9DF" in projection.html
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
async def test_weekly_email_unknown_outcome_is_terminal_without_retry(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)

    class UncertainEmailService:
        def __init__(self) -> None:
            self.attempts = 0

        def send(self, message: EmailMessage) -> None:
            self.attempts += 1
            raise EmailSendOutcomeUnknown("handoff outcome unknown")

    email = UncertainEmailService()
    workflow = WeeklyGrowthEmailWorkflow(
        sessions=sessions, email_service=email, frontend_url="https://app.example"
    )
    first = await workflow.deliver(
        graph.plan.id, graph.workspace.id, graph.product.id, graph.plan.plan_revision or 1
    )
    replay = await workflow.deliver(
        graph.plan.id, graph.workspace.id, graph.product.id, graph.plan.plan_revision or 1
    )

    assert first["status"] == replay["status"] == "unknown_outcome"
    assert replay["duplicate"] is True
    assert email.attempts == 1


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
        draft_content = await db.scalar(select(drafts.c.content))
    assert first == replay and first is not None
    assert draft_count == 1
    assert draft_content == "Ship it"


@pytest.mark.asyncio
async def test_x_asset_bridge_rejects_malformed_structured_content(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    async with sessions() as db:
        asset = await db.get(PreparedAsset, graph.asset.id)
        assert asset is not None
        asset.structured_content = {"kind": "x_draft", "content": {"title": "wrong"}}
        await db.flush()
        with pytest.raises(ValueError, match="invalid_x_asset_content"):
            await project_x_asset_to_draft(
                db,
                workspace_id=graph.workspace.id,
                product_id=graph.product.id,
                asset_id=graph.asset.id,
                revision=1,
            )


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
        disabled = await db.get(Product, graph.empty_product.id)
        assert disabled is not None
        disabled.operator_manual_enabled = False
        await db.commit()
    with pytest.raises(ConflictError, match="disabled") as error:
        async with sessions() as db:
            await request_manual_weekly_run(
                db,
                workspace_id=graph.workspace.id,
                product_id=graph.empty_product.id,
                actor_id=graph.owner.id,
                period_start=NOW,
                period_end=NOW + timedelta(days=7),
                workflow_version="v1",
                idempotency_key="disabled",
            )
    assert error.value.code == "manual_runs_disabled"
    async with sessions() as db:
        enabled = await db.get(Product, graph.empty_product.id)
        assert enabled is not None
        enabled.weekly_growth_operator_enabled = True
        enabled.operator_manual_enabled = True
        await db.commit()
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
            product_id=graph.product.id,
            actor_id=graph.owner.id,
            period_start=NOW + timedelta(days=7),
            period_end=NOW + timedelta(days=14),
            workflow_version="v2",
            idempotency_key="manual-run",
        )
    async with sessions() as db:
        persisted = await db.get(OperatorRun, run.id)
        event_count = await db.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.aggregate_id == run.id)
        )
        assert persisted is not None and persisted.trigger == "manual"
        assert event_count == 1


def test_manual_weekly_run_requires_both_operator_flags(phase5_client: Phase5Client) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    url = f"{root(graph, graph.empty_product).removesuffix('/operator')}/operator/runs"
    response = client.post(
        url,
        headers=mutation_headers(graph.owner, "manual-disabled"),
        json={"runKind": "weekly_growth"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "manual_runs_disabled"

    async def enable_operator() -> None:
        async with sessions() as db:
            product = await db.get(Product, graph.empty_product.id)
            assert product is not None
            product.weekly_growth_operator_enabled = True
            product.operator_manual_enabled = True
            await db.commit()

    client.portal.call(enable_operator)
    enabled = client.post(
        url,
        headers=mutation_headers(graph.owner, "manual-enabled"),
        json={"runKind": "weekly_growth"},
    )
    assert enabled.status_code == 409
    assert enabled.json()["code"] == "activation_product_mismatch"


class UnusedDependency:
    async def process(self, *args: object) -> dict[str, object]:
        raise AssertionError("dependency must not run")


class StoppedDetection:
    async def process(self, *args: object) -> dict[str, object]:
        return {"status": "stopped", "summary": {"reason": "no_sources"}}


class FailedDetection:
    async def process(self, *args: object) -> dict[str, object]:
        raise RuntimeError("provider payload must not be persisted")


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


@pytest.mark.asyncio
async def test_weekly_workflow_persists_sanitized_terminal_failure(
    phase5_client: Phase5Client,
) -> None:
    client, sessions = phase5_client
    assert client.portal is not None
    graph = client.portal.call(seed_graph, sessions)
    async with sessions() as db:
        failed_run = OperatorRun(
            workspace_id=graph.workspace.id,
            product_id=graph.empty_product.id,
            run_kind="weekly_growth",
            trigger="manual",
            status="pending",
            idempotency_key="failed-weekly-run",
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
        db.add(failed_run)
        await db.commit()
        failed_run_id = failed_run.id

    workflow = WeeklyOperatorWorkflow(
        sessions=sessions,
        detection=cast(Any, FailedDetection()),
        ai=cast(Any, UnusedDependency()),
    )
    with pytest.raises(RuntimeError, match="provider payload"):
        await workflow.process(failed_run_id, graph.workspace.id, graph.empty_product.id)

    async with sessions() as db:
        persisted = await db.get(OperatorRun, failed_run_id)
        assert persisted is not None
        assert persisted.status == "failed"
        assert persisted.failure_category == "RuntimeError"
        assert persisted.completed_at is not None
        stage_summary = persisted.stage_summary
        assert stage_summary is not None
        assert stage_summary == {
            "detectionRunId": stage_summary["detectionRunId"],
            "failure": {"category": "RuntimeError", "stage": "detecting"},
        }
        assert "provider payload" not in str(stage_summary)
