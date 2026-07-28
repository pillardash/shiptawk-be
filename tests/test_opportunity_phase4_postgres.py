import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.modules.operator.models import (
    OperatorRun,
    Opportunity,
    OpportunityEvaluation,
    OpportunityFeedback,
    OpportunityStatusEvent,
)
from app.modules.operator.schemas import OpportunityFeedbackCommand
from app.modules.operator.services.opportunity_detection_service import persist_detection
from app.modules.operator.services.opportunity_feedback_service import (
    append_feedback,
    dismiss_opportunity,
)
from app.modules.operator.services.opportunity_snapshot_service import DetectionInputAssembly
from app.modules.products.models import Product
from tests.test_opportunity_snapshot_integration import NOW, assembled, seed_graph

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")


def postgres_sessions() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def seed_detection(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, UUID, UUID, DetectionInputAssembly]:
    async with sessions.begin() as db:
        graph = await seed_graph(db, label=f"pg-{uuid4()}")
        assembly = await assembled(db, graph)
        run = OperatorRun(
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            run_kind="opportunity_detection",
            trigger="manual",
            status="running",
            idempotency_key=f"detection-{uuid4()}",
            request_fingerprint="r" * 64,
            analyzer_version="v1",
            detector_suite_version="v1",
            quality_policy_version="v1",
            scoring_policy_version="v1",
            cooldown_policy_version="v1",
            provenance={},
            summary={},
            created_by_actor_id=graph.user_id,
            as_of=NOW,
        )
        db.add(run)
        await db.flush()
        return graph.workspace_id, graph.product_id, graph.user_id, run.id, assembly


async def seed_open_opportunity(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID, UUID, UUID]:
    async with sessions.begin() as db:
        graph = await seed_graph(db, label=f"feedback-{uuid4()}")
        opportunity = await db.get(Opportunity, graph.opportunity_id)
        assert opportunity is not None
        opportunity.status = "open"
        opportunity.cooldown_until = NOW + timedelta(days=30)
        run = OperatorRun(
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            run_kind="opportunity_detection",
            trigger="manual",
            status="completed",
            idempotency_key=f"feedback-run-{uuid4()}",
            request_fingerprint="r" * 64,
            analyzer_version="v1",
            provenance={},
            summary={},
            created_by_actor_id=graph.user_id,
        )
        db.add(run)
        await db.flush()
        db.add(
            OpportunityEvaluation(
                workspace_id=graph.workspace_id,
                product_id=graph.product_id,
                operator_run_id=run.id,
                accepted_opportunity_id=opportunity.id,
                detector_id="postgres",
                detector_version="v1",
                outcome="accepted",
                evaluation_key=uuid4().hex,
                reason_codes=[],
                candidate_snapshot={"candidate": "safe"},
                diagnostics={},
                input_schema_version="1",
                detector_suite_version="v1",
                quality_policy_version="v1",
                scoring_policy_version="v1",
                cooldown_policy_version="v1",
                score_version="v1",
            )
        )
        return graph.workspace_id, graph.product_id, graph.user_id, graph.opportunity_id


@pytest.mark.asyncio
async def test_concurrent_detection_persistence_replays_without_integrity_error() -> None:
    engine, sessions = postgres_sessions()
    workspace_id, product_id, _, run_id, assembly = await seed_detection(sessions)

    async def persist() -> tuple[UUID, ...]:
        async with sessions() as db:
            rows = await persist_detection(
                db,
                workspace_id=workspace_id,
                product_id=product_id,
                operator_run_id=run_id,
                assembly=assembly,
            )
            return tuple(row.id for row in rows)

    first, second = await asyncio.gather(persist(), persist())
    assert first == second
    async with sessions() as db:
        evaluation_count = await db.scalar(
            select(func.count())
            .select_from(OpportunityEvaluation)
            .where(OpportunityEvaluation.operator_run_id == run_id)
        )
        opportunity_count = await db.scalar(
            select(func.count())
            .select_from(Opportunity)
            .where(Opportunity.operator_run_id == run_id)
        )
        assert evaluation_count == len(first)
        assert opportunity_count == sum(
            1
            for row in await db.scalars(
                select(OpportunityEvaluation).where(
                    OpportunityEvaluation.operator_run_id == run_id,
                    OpportunityEvaluation.accepted_opportunity_id.is_not(None),
                )
            )
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_standalone_feedback_replays_one_row() -> None:
    engine, sessions = postgres_sessions()
    workspace_id, product_id, actor_id, opportunity_id = await seed_open_opportunity(sessions)
    key = f"feedback-{uuid4()}"
    command = OpportunityFeedbackCommand(
        opportunity_id=opportunity_id,
        reason_code="useful",
        usefulness=5,
        comment="Useful",
    )

    async def append() -> UUID:
        async with sessions() as db:
            return (
                await append_feedback(
                    db,
                    workspace_id=workspace_id,
                    product_id=product_id,
                    actor_id=actor_id,
                    idempotency_key=key,
                    command=command,
                )
            ).id

    ids = await asyncio.gather(append(), append())
    assert ids[0] == ids[1]
    async with sessions() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(OpportunityFeedback)
            .where(OpportunityFeedback.idempotency_key == key)
        )
        assert count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_dismissal_replays_one_status_event() -> None:
    engine, sessions = postgres_sessions()
    workspace_id, product_id, actor_id, opportunity_id = await seed_open_opportunity(sessions)
    key = f"dismiss-{uuid4()}"
    command = OpportunityFeedbackCommand(
        opportunity_id=opportunity_id,
        reason_code="not_relevant",
        comment="Not relevant",
    )

    async def dismiss() -> UUID:
        async with sessions() as db:
            return (
                await dismiss_opportunity(
                    db,
                    workspace_id=workspace_id,
                    product_id=product_id,
                    actor_id=actor_id,
                    idempotency_key=key,
                    command=command,
                )
            ).id

    ids = await asyncio.gather(dismiss(), dismiss())
    assert ids[0] == ids[1]
    async with sessions() as db:
        event_count = await db.scalar(
            select(func.count())
            .select_from(OpportunityStatusEvent)
            .where(OpportunityStatusEvent.feedback_id == ids[0])
        )
        assert event_count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_cross_product_opportunity_run_fk_is_rejected() -> None:
    engine, sessions = postgres_sessions()
    workspace_id, _product_id, actor_id, run_id, _ = await seed_detection(sessions)
    async with sessions() as db:
        other = Product(workspace_id=workspace_id, user_id=actor_id, name=f"Other {uuid4()}")
        db.add(other)
        await db.flush()
        db.add(
            Opportunity(
                workspace_id=workspace_id,
                product_id=other.id,
                operator_run_id=run_id,
                title="Invalid",
                evidence_ids=[],
                interpretation="Invalid",
                recommendation="Invalid",
                confidence="low",
                missing_information=[],
                approval_level="review",
                measurement_window="28_days",
                stop_conditions=[],
                impact_score=1,
                effort_score=1,
                urgency_score=1,
                status="open",
                cooldown_dedup_key=str(uuid4()),
                cooldown_until=datetime.now(UTC),
                provenance={},
                created_by_actor_id=actor_id,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_opportunity_delete_is_restricted_by_evidence_and_feedback() -> None:
    engine, sessions = postgres_sessions()
    workspace_id, product_id, _, opportunity_id = await seed_open_opportunity(sessions)
    async with sessions() as db:
        with pytest.raises(IntegrityError):
            await db.execute(
                delete(Opportunity).where(
                    Opportunity.workspace_id == workspace_id,
                    Opportunity.product_id == product_id,
                    Opportunity.id == opportunity_id,
                )
            )
            await db.flush()
        await db.rollback()
    await engine.dispose()
