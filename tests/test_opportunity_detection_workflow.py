import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import StaticPool, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.modules.identity.models.users import User
from app.modules.operator.models import (
    OperatorRun,
    OperatorRunSnapshot,
    Opportunity,
    OpportunityEvaluation,
)
from app.modules.operator.schemas import DetectionInput
from app.modules.operator.services.opportunity_snapshot_service import (
    MissingApprovedProfileError,
    canonicalize_detection_input,
)
from app.modules.products.models import Product
from app.modules.repos.models import repos  # noqa: F401
from app.modules.workspaces.models import Workspace
from app.workflows.opportunity_detection import (
    OpportunityDetectionLeaseBusyError,
    OpportunityDetectionLeaseLostError,
    OpportunityDetectionTenantMismatchError,
    OpportunityDetectionWorkflow,
)

NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)


@pytest.fixture
async def workflow_db() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    await engine.dispose()


async def seed_run(
    sessions: async_sessionmaker[AsyncSession],
    *,
    status: str = "pending",
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    as_of: datetime | None = None,
) -> tuple[UUID, UUID, UUID]:
    async with sessions() as db:
        user, workspace = User(email=f"workflow-{uuid4()}@example.com"), Workspace(name="Workflow")
        db.add_all([user, workspace])
        await db.flush()
        product = Product(workspace_id=workspace.id, user_id=user.id, name="Product")
        db.add(product)
        await db.flush()
        run = OperatorRun(
            workspace_id=workspace.id,
            product_id=product.id,
            run_kind="opportunity_detection",
            trigger="manual",
            status=status,
            idempotency_key=str(uuid4()),
            request_fingerprint="r" * 64,
            analyzer_version="v1",
            provenance={},
            summary={},
            created_by_actor_id=user.id,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            as_of=as_of,
        )
        db.add(run)
        await db.commit()
        return run.id, workspace.id, product.id


async def load(sessions: async_sessionmaker[AsyncSession], run_id: UUID) -> OperatorRun:
    async with sessions() as db:
        run = await db.get(OperatorRun, run_id)
        assert run is not None
        return run


@pytest.mark.asyncio
async def test_event_tenant_mismatch_is_rejected(
    workflow_db: async_sessionmaker[AsyncSession],
) -> None:
    run_id, _workspace_id, product_id = await seed_run(workflow_db)
    workflow = OpportunityDetectionWorkflow(sessions=workflow_db, now=lambda: NOW)
    with pytest.raises(OpportunityDetectionTenantMismatchError):
        await workflow.process(run_id, uuid4(), product_id)
    assert (await load(workflow_db, run_id)).status == "pending"


@pytest.mark.asyncio
async def test_duplicate_terminal_delivery_replays_and_clears_stale_lease(
    workflow_db: async_sessionmaker[AsyncSession],
) -> None:
    run_id, workspace_id, product_id = await seed_run(
        workflow_db, status="completed", lease_owner="stale", lease_expires_at=NOW
    )
    result = await OpportunityDetectionWorkflow(sessions=workflow_db, now=lambda: NOW).process(
        run_id, workspace_id, product_id
    )
    run = await load(workflow_db, run_id)
    assert result["status"] == "completed"
    assert run.lease_owner is None
    assert run.lease_expires_at is None


@pytest.mark.asyncio
async def test_unexpired_lease_blocks_concurrent_delivery(
    workflow_db: async_sessionmaker[AsyncSession],
) -> None:
    run_id, workspace_id, product_id = await seed_run(
        workflow_db,
        status="running",
        lease_owner="active",
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(OpportunityDetectionLeaseBusyError):
        await OpportunityDetectionWorkflow(sessions=workflow_db, now=lambda: NOW).process(
            run_id, workspace_id, product_id
        )


@pytest.mark.asyncio
async def test_expired_lease_is_taken_over(
    workflow_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, workspace_id, product_id = await seed_run(
        workflow_db,
        status="running",
        lease_owner="old",
        lease_expires_at=NOW - timedelta(seconds=1),
    )

    async def missing(*_args: Any, **_kwargs: Any) -> None:
        raise MissingApprovedProfileError("missing")

    monkeypatch.setattr("app.workflows.opportunity_detection.assemble_detection_input", missing)
    result = await OpportunityDetectionWorkflow(sessions=workflow_db, now=lambda: NOW).process(
        run_id, workspace_id, product_id
    )
    assert result["status"] == "stopped"
    assert result["summary"] == {"reason": "missing_approved_profile"}


@pytest.mark.asyncio
async def test_first_claim_preserves_as_of_across_retry(
    workflow_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = NOW - timedelta(days=2)
    run_id, workspace_id, product_id = await seed_run(workflow_db, as_of=original)
    observed: list[datetime] = []

    async def fail(_db: AsyncSession, _workspace: UUID, _product: UUID, as_of: datetime) -> None:
        observed.append(as_of)
        raise RuntimeError("retry")

    monkeypatch.setattr("app.workflows.opportunity_detection.assemble_detection_input", fail)
    workflow = OpportunityDetectionWorkflow(sessions=workflow_db, now=lambda: NOW)
    with pytest.raises(RuntimeError, match="retry"):
        await workflow.process(run_id, workspace_id, product_id)
    assert observed == [original]
    assert (await load(workflow_db, run_id)).as_of == original.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_unexpected_failure_resets_and_rethrows(
    workflow_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, workspace_id, product_id = await seed_run(workflow_db)

    async def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("app.workflows.opportunity_detection.assemble_detection_input", fail)
    with pytest.raises(RuntimeError, match="boom"):
        await OpportunityDetectionWorkflow(sessions=workflow_db, now=lambda: NOW).process(
            run_id, workspace_id, product_id
        )
    run = await load(workflow_db, run_id)
    assert (run.status, run.failure_category, run.lease_owner, run.lease_expires_at) == (
        "pending",
        "unexpected",
        None,
        None,
    )


@pytest.mark.asyncio
async def test_lost_owner_is_fenced_during_renewal(
    workflow_db: async_sessionmaker[AsyncSession],
) -> None:
    run_id, _, _ = await seed_run(
        workflow_db,
        status="running",
        lease_owner="real",
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    workflow = OpportunityDetectionWorkflow(sessions=workflow_db, now=lambda: NOW)
    with pytest.raises(OpportunityDetectionLeaseLostError):
        await workflow._renew(run_id, "impostor")


@pytest.mark.asyncio
async def test_missing_run_returns_not_found(
    workflow_db: async_sessionmaker[AsyncSession],
) -> None:
    run_id = uuid4()
    result = await OpportunityDetectionWorkflow(sessions=workflow_db, now=lambda: NOW).process(
        run_id, uuid4(), uuid4()
    )
    assert result == {"status": "not_found", "runId": str(run_id)}


@pytest.mark.asyncio
async def test_stale_worker_commits_nothing_after_lease_is_stolen_post_assembly(
    workflow_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, workspace_id, product_id = await seed_run(workflow_db)
    dataset = Path(__file__).parent / "evaluations/opportunity_engine/v1/dataset.json"
    raw = json.loads(dataset.read_text())
    data = DetectionInput.model_validate(raw["input"]).model_copy(
        update={"workspace_id": workspace_id, "product_id": product_id}
    )
    assembly = canonicalize_detection_input(data)
    calls = 0

    async def controlled(*_args: Any, **_kwargs: Any):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            async with workflow_db() as other:
                await other.execute(
                    update(OperatorRun)
                    .where(OperatorRun.id == run_id)
                    .values(lease_owner="winner", lease_expires_at=NOW - timedelta(seconds=1))
                )
                await other.commit()
        return assembly

    monkeypatch.setattr("app.workflows.opportunity_detection.assemble_detection_input", controlled)
    workflow = OpportunityDetectionWorkflow(sessions=workflow_db, now=lambda: NOW)
    with pytest.raises(OpportunityDetectionLeaseLostError):
        await workflow.process(run_id, workspace_id, product_id)

    async with workflow_db() as db:
        counts = [
            await db.scalar(
                select(func.count()).select_from(model).where(model.operator_run_id == run_id)
            )
            for model in (OperatorRunSnapshot, OpportunityEvaluation, Opportunity)
        ]
    assert counts == [0, 0, 0]

    result = await workflow.process(run_id, workspace_id, product_id)
    assert result["status"] == "completed"
    async with workflow_db() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(OperatorRunSnapshot)
                .where(OperatorRunSnapshot.operator_run_id == run_id)
            )
            == 1
        )
