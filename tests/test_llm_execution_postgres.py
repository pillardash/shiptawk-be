import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.modules.llm.models.llm_execution import LLMExecution
from app.modules.llm.models.llm_execution_attempt import LLMExecutionAttempt
from app.modules.llm.policies.llm_route_policy import (
    LLMModelRegistration,
    LLMRoutePlan,
    LLMRoutePolicy,
    LLMRouteStep,
    ModelPricing,
)
from app.modules.llm.providers.fake.fake_llm_provider import FakeLLMProvider
from app.modules.llm.providers.generation_result import LLMGenerationResult, LLMGenerationTelemetry
from app.modules.llm.providers.llm_exceptions import LLMTransientError
from app.modules.llm.providers.prompt_envelope import PromptEnvelope, PromptMessage
from app.modules.llm.providers.provider_registry import LLMProviderRegistry
from app.modules.llm.repositories import llm_execution_repository as repository
from app.modules.llm.services.llm_execution_service import (
    LLMExecutionInProgressError,
    LLMExecutionService,
    LLMIdempotencyConflictError,
    PersistedLLMExecutionResult,
)
from tests.test_opportunity_snapshot_integration import seed_graph

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")


class TransientProvider:
    name = "primary"

    async def generate(self, envelope: PromptEnvelope) -> LLMGenerationResult:
        raise LLMTransientError("temporary")


class SlowProvider:
    name = "slow"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, envelope: PromptEnvelope) -> LLMGenerationResult:
        self.calls += 1
        await asyncio.sleep(0.35)
        return LLMGenerationResult(
            output={"ok": True},
            telemetry=LLMGenerationTelemetry(
                provider="slow", model=envelope.model, correlation_id="slow-test"
            ),
        )


def envelope(content: str = "sanitized") -> PromptEnvelope:
    return PromptEnvelope(
        model="route-owned",
        prompt_id="test",
        prompt_version="v1",
        input_schema_version="v1",
        output_schema_version="v1",
        messages=(PromptMessage(role="user", content=content),),
        output_schema_name="result",
        output_json_schema={"type": "object"},
        correlation_id=uuid4().hex,
    )


def service(
    sessions: async_sessionmaker[AsyncSession], fake: FakeLLMProvider
) -> LLMExecutionService:
    pricing = ModelPricing(input_per_million=Decimal("1"), output_per_million=Decimal("2"))
    registrations = {
        "primary": LLMModelRegistration(
            name="primary",
            provider="primary",
            model="primary-model",
            capabilities={"structured_output"},
            pricing=pricing,
            pricing_version="v1",
        ),
        "fallback": LLMModelRegistration(
            name="fallback",
            provider="fake",
            model="fallback-model",
            capabilities={"structured_output"},
            pricing=pricing,
            pricing_version="v1",
        ),
    }
    routes = LLMRoutePolicy(
        registrations,
        {
            "content": LLMRoutePlan(
                name="content",
                steps=(LLMRouteStep(model="primary"), LLMRouteStep(model="fallback")),
                max_attempts=2,
            )
        },
    )
    return LLMExecutionService(
        sessions,
        LLMProviderRegistry({"primary": TransientProvider(), "fake": fake}),
        routes,
    )


@pytest.mark.asyncio
async def test_same_key_has_one_lease_winner_and_records_fallback_attempts() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as db:
        graph = await seed_graph(db, label=f"llm-{uuid4()}")
    fake = FakeLLMProvider(
        LLMGenerationResult(
            output={"ok": True},
            telemetry=LLMGenerationTelemetry(
                provider="fake", model="ignored", correlation_id="correlation"
            ),
        )
    )
    executor = service(sessions, fake)
    idempotency_key = uuid4().hex
    request = envelope()

    async def execute(
        request_envelope: PromptEnvelope = request,
    ) -> PersistedLLMExecutionResult:
        return await executor.execute(
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            use_case="test",
            idempotency_key=idempotency_key,
            route_name="content",
            envelope=request_envelope,
            validate_output=lambda value: {"ok": bool(cast(dict[str, object], value)["ok"])},
            lease_owner="worker",
        )

    outcomes = await asyncio.gather(execute(), execute(), return_exceptions=True)

    assert all(
        not isinstance(item, BaseException) or isinstance(item, LLMExecutionInProgressError)
        for item in outcomes
    )
    assert len(fake.requests) == 1
    replay = await execute()
    assert replay.replayed is True
    assert replay.validated_output == {"ok": True}
    async with sessions() as db:
        execution = await db.scalar(
            select(LLMExecution).where(LLMExecution.idempotency_key == idempotency_key)
        )
        assert execution is not None
        attempts = list(
            await db.scalars(
                select(LLMExecutionAttempt)
                .where(LLMExecutionAttempt.execution_id == execution.id)
                .order_by(LLMExecutionAttempt.attempt_number)
            )
        )
        assert [(item.provider, item.status) for item in attempts] == [
            ("primary", "failed"),
            ("fake", "completed"),
        ]
    with pytest.raises(LLMIdempotencyConflictError):
        await execute(envelope("different"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_domain_output_is_not_persisted_or_sent_to_fallback() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as db:
        graph = await seed_graph(db, label=f"invalid-llm-{uuid4()}")
    fake = FakeLLMProvider(
        LLMGenerationResult(
            output={"privateProviderBody": "must not persist"},
            telemetry=LLMGenerationTelemetry(
                provider="fake", model="ignored", correlation_id="correlation"
            ),
        )
    )
    executor = service(sessions, fake)

    with pytest.raises(Exception, match="llm_output_validation_failed"):
        await executor.execute(
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            use_case="test-invalid",
            idempotency_key=uuid4().hex,
            route_name="content",
            envelope=envelope(),
            validate_output=lambda value: (_ for _ in ()).throw(ValueError("invalid")),
            lease_owner="worker",
        )

    async with sessions() as db:
        execution = await db.scalar(
            select(LLMExecution).where(LLMExecution.use_case == "test-invalid")
        )
        assert execution is not None
        assert execution.validated_output_snapshot is None
        assert "privateProviderBody" not in str(execution.__dict__)
    assert len(fake.requests) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_lease_is_fenced_and_only_one_successor_wins() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as db:
        graph = await seed_graph(db, label=f"lease-{uuid4()}")
        execution = LLMExecution(
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            use_case="lease",
            idempotency_key=uuid4().hex,
            request_fingerprint="f" * 64,
            route_name="content",
            route_snapshot={},
            sanitized_metadata={},
            status="pending",
        )
        await repository.flush_execution(db, execution)
    expired_token = uuid4()
    now = datetime.now(UTC)
    async with sessions.begin() as db:
        execution.lease_token = expired_token
        execution.lease_owner = "old"
        execution.lease_expires_at = now - timedelta(seconds=1)
        execution.status = "running"
        db.add(execution)

    async def acquire(owner: str) -> bool:
        async with sessions.begin() as db:
            return await repository.acquire_lease(
                db,
                execution.id,
                graph.workspace_id,
                graph.product_id,
                owner=owner,
                token=uuid4(),
                now=now,
                expires_at=now + timedelta(minutes=1),
            )

    assert sum(await asyncio.gather(acquire("one"), acquire("two"))) == 1
    async with sessions.begin() as db:
        assert not await repository.fence_update(
            db,
            execution.id,
            graph.workspace_id,
            graph.product_id,
            expired_token,
            status="completed",
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_slow_provider_heartbeat_prevents_expired_lease_duplicate_call() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as db:
        graph = await seed_graph(db, label=f"heartbeat-{uuid4()}")
    slow = SlowProvider()
    registration = LLMModelRegistration(
        name="slow",
        provider="slow",
        model="slow-v1",
        capabilities={"structured_output"},
        pricing=ModelPricing(input_per_million=Decimal(0), output_per_million=Decimal(0)),
        pricing_version="test",
    )
    executor = LLMExecutionService(
        sessions,
        LLMProviderRegistry({"slow": slow}),
        LLMRoutePolicy(
            {"slow": registration},
            {
                "slow": LLMRoutePlan(
                    name="slow", steps=(LLMRouteStep(model="slow"),), max_attempts=1
                )
            },
        ),
        lease_duration=timedelta(milliseconds=150),
    )
    key = uuid4().hex

    async def execute() -> PersistedLLMExecutionResult:
        return await executor.execute(
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            use_case="heartbeat",
            idempotency_key=key,
            route_name="slow",
            envelope=envelope(),
            validate_output=lambda value: cast(dict[str, object], value),
            lease_owner="worker",
        )

    first = asyncio.create_task(execute())
    await asyncio.sleep(0.22)
    with pytest.raises(LLMExecutionInProgressError):
        await execute()
    await first
    assert slow.calls == 1
    await engine.dispose()
