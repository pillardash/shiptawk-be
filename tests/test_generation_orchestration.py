from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import StaticPool, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.domains.generation.context import (
    GenerationContextBuilder,
    ProductContextInput,
    RepositoryContextInput,
    SafetySettingsInput,
    UserContextInput,
    VoiceProfileInput,
)
from app.domains.generation.fake_provider import DeterministicGenerationProvider
from app.domains.generation.models import (
    GenerationRequest,
    GenerationTelemetry,
    ProviderGenerationResult,
    StructuredGenerationOutput,
)
from app.domains.generation.provider import ProviderPermanentError, ProviderTransientError
from app.domains.generation.repository import GenerationRepository
from app.domains.generation.service import (
    GenerationInput,
    GenerationOrchestrator,
    InvalidGenerationOutput,
    TransientGenerationFailure,
)
from app.domains.integrations.event_normalizer import NormalizedEvent
from app.domains.legacy.models import generation_runs, tweet_candidates
from app.domains.users import models as user_models  # noqa: F401
from app.domains.workspaces import models as workspace_models  # noqa: F401


@pytest.fixture
async def sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield session_factory
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def generation_input(*, stable_key: str = "event:release:42") -> GenerationInput:
    context = GenerationContextBuilder().build(
        user=UserContextInput(
            tone_preference="technical",
            voice_profile=VoiceProfileInput(audience_preference="Product teams"),
            safety_settings=SafetySettingsInput(
                preferred_event_types=["release"],
                max_drafts_per_week=10,
                private_repo_mode="normal",
            ),
        ),
        repo=RepositoryContextInput(repo_name="shiptawk", repo_full_name="acme/shiptawk"),
        product=ProductContextInput(
            name="Shiptawk",
            description="Turns shipped work into evidence-backed marketing.",
            target_audience="Product teams",
            messaging_angle="Lead with user value",
        ),
        style_anchors=[],
    )
    event = NormalizedEvent(
        event_type="release",
        repo_full_name="acme/shiptawk",
        title="Shiptawk release v2",
        description="Product teams can now turn release updates into marketing posts.",
        url="https://github.com/acme/shiptawk/releases/v2",
        occurred_at="2026-07-22T12:00:00Z",
        sha=None,
        branch_name=None,
        touched_paths=[],
        dedupe_key="release|acme/shiptawk||2026-07-22T12:00:00Z|v2",
    )
    return GenerationInput(
        workspace_id=uuid4(),
        user_id=uuid4(),
        repo_id=uuid4(),
        product_id=uuid4(),
        normalized_event_id=uuid4(),
        stable_key=stable_key,
        correlation_id="corr-42",
        event=event,
        context=context,
        repo_private=False,
        drafts_created_this_week=0,
    )


def valid_provider_result() -> ProviderGenerationResult:
    return ProviderGenerationResult(
        output={
            "candidates": [
                {
                    "content": (
                        "Shiptawk now helps product teams turn release updates "
                        "into marketing posts."
                    ),
                    "variant": "customer_value",
                    "angle": "user_benefit",
                    "rationale": "Leads with the supported user benefit.",
                    "evidenceIds": ["event.change.2", "product.identity.1"],
                }
            ]
        },
        telemetry=GenerationTelemetry(
            provider="deterministic",
            model="fake-v1",
            latency_ms=12,
            input_tokens=30,
            output_tokens=18,
            total_tokens=48,
            cost_estimate_usd=0,
            correlation_id="provider-corr-42",
        ),
    )


def test_structured_output_rejects_unknown_fields_and_invalid_variants() -> None:
    with pytest.raises(ValidationError):
        StructuredGenerationOutput.model_validate(
            {
                "candidates": [
                    {
                        "content": "A plausible result",
                        "variant": "unpersistable_variant",
                        "angle": "user_benefit",
                        "rationale": "Reason",
                        "evidenceIds": [],
                        "rawPrompt": "must not be accepted",
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_fake_provider_is_deterministic_and_records_requests() -> None:
    result = valid_provider_result()
    provider = DeterministicGenerationProvider(result=result)
    request = GenerationRequest(
        model="fake-v1",
        prompt_version="tweet-candidates.v1",
        system_instructions="Return grounded candidates.",
        input_text="Sanitized public context",
        parameters={"temperature": 0},
        correlation_id="corr-42",
    )

    assert await provider.generate(request) == result
    assert await provider.generate(request) == result
    assert provider.requests == [request, request]


@pytest.mark.asyncio
async def test_orchestrator_persists_ranked_candidates_and_reuses_stable_run(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payload = generation_input()
    provider = DeterministicGenerationProvider(result=valid_provider_result())
    async with sessions() as db:
        orchestrator = GenerationOrchestrator(
            repository=GenerationRepository(db), provider=provider, model="fake-v1"
        )

        first = await orchestrator.run(payload)
        second = await orchestrator.run(payload)

        run_row = (
            (
                await db.execute(
                    select(generation_runs).where(
                        generation_runs.c.workspace_id == payload.workspace_id
                    )
                )
            )
            .mappings()
            .one()
        )
        candidates = (
            (
                await db.execute(
                    select(tweet_candidates).where(
                        tweet_candidates.c.workspace_id == payload.workspace_id
                    )
                )
            )
            .mappings()
            .all()
        )

    assert first.outcome == "completed"
    assert first.reused is False
    assert second.reused is True
    assert second.run_id == first.run_id
    assert provider.call_count == 1
    assert len(candidates) == 1
    assert candidates[0]["rank"] == 1
    assert run_row["provider"] == "deterministic"
    assert run_row["model"] == "fake-v1"
    assert run_row["system_prompt"] == "[not persisted]"
    assert run_row["user_prompt"] == "[not persisted]"
    assert run_row["provider_parameters"]["promptVersion"] == "tweet-candidates.v1"
    assert run_row["provider_parameters"]["correlationId"] == "provider-corr-42"
    assert run_row["provider_parameters"]["usage"]["totalTokens"] == 48


@pytest.mark.asyncio
async def test_repository_lookup_is_workspace_scoped(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payload = generation_input()
    async with sessions() as db:
        repository = GenerationRepository(db)
        orchestrator = GenerationOrchestrator(
            repository=repository,
            provider=DeterministicGenerationProvider(result=valid_provider_result()),
            model="fake-v1",
        )
        completed = await orchestrator.run(payload)

        assert completed.run_id is not None
        assert await repository.get_run(payload.workspace_id, completed.run_id) is not None
        assert await repository.get_run(uuid4(), completed.run_id) is None


@pytest.mark.asyncio
async def test_transient_provider_failure_is_classified_and_safely_persisted(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payload = generation_input()
    provider = DeterministicGenerationProvider(
        error=ProviderTransientError("upstream included private prompt text")
    )
    async with sessions() as db:
        orchestrator = GenerationOrchestrator(
            repository=GenerationRepository(db), provider=provider, model="fake-v1"
        )
        with pytest.raises(TransientGenerationFailure):
            await orchestrator.run(payload)
        row = (await db.execute(select(generation_runs))).mappings().one()

    assert row["status"] == "failed"
    assert row["failure_message"] == "transient_provider_failure"
    assert "private prompt" not in str(row)


@pytest.mark.asyncio
async def test_transient_provider_failure_is_retried_with_the_same_stable_run_id(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    class FlakyProvider:
        name = "deterministic"

        def __init__(self) -> None:
            self.call_count = 0

        async def generate(self, request: GenerationRequest) -> ProviderGenerationResult:
            del request
            self.call_count += 1
            if self.call_count == 1:
                raise ProviderTransientError("temporary")
            return valid_provider_result()

    payload = generation_input()
    provider = FlakyProvider()
    async with sessions() as db:
        orchestrator = GenerationOrchestrator(
            repository=GenerationRepository(db), provider=provider, model="fake-v1"
        )
        with pytest.raises(TransientGenerationFailure):
            await orchestrator.run(payload)
        completed = await orchestrator.run(payload)
        rows = (await db.execute(select(generation_runs))).mappings().all()

    assert completed.outcome == "completed"
    assert completed.run_id == GenerationRepository.run_id(payload.workspace_id, payload.stable_key)
    assert provider.call_count == 2
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_invalid_structured_output_is_non_retryable_and_persisted(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payload = generation_input()
    malformed = ProviderGenerationResult(
        output={"candidates": [{"content": "missing required fields"}]},
        telemetry=valid_provider_result().telemetry,
    )
    async with sessions() as db:
        orchestrator = GenerationOrchestrator(
            repository=GenerationRepository(db),
            provider=DeterministicGenerationProvider(result=malformed),
            model="fake-v1",
        )
        with pytest.raises(InvalidGenerationOutput):
            await orchestrator.run(payload)
        row = (await db.execute(select(generation_runs))).mappings().one()

    assert row["status"] == "failed"
    assert row["failure_message"] == "invalid_structured_output"
    assert row["provider_parameters"]["failureCategory"] == "invalid_output"


@pytest.mark.asyncio
async def test_permanent_provider_failure_is_controlled_and_non_retryable(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payload = generation_input()
    provider = DeterministicGenerationProvider(
        error=ProviderPermanentError("provider rejected request")
    )
    async with sessions() as db:
        orchestrator = GenerationOrchestrator(
            repository=GenerationRepository(db), provider=provider, model="fake-v1"
        )
        with pytest.raises(InvalidGenerationOutput, match="invalid_provider_response"):
            await orchestrator.run(payload)
        row = (await db.execute(select(generation_runs))).mappings().one()

    assert row["status"] == "failed"
    assert row["failure_message"] == "invalid_provider_response"
    assert row["provider_parameters"]["failureCategory"] == "provider_error"


@pytest.mark.asyncio
async def test_non_generate_score_does_not_call_provider_or_persist_run(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payload = generation_input().model_copy(
        update={
            "event": generation_input().event.model_copy(
                update={"title": "Secret token rotation", "description": "Internal security fix"}
            )
        }
    )
    provider = DeterministicGenerationProvider(result=valid_provider_result())
    async with sessions() as db:
        result = await GenerationOrchestrator(
            repository=GenerationRepository(db), provider=provider, model="fake-v1"
        ).run(payload)
        run_count = len((await db.execute(select(generation_runs))).all())

    assert result.outcome == "block"
    assert result.run_id is None
    assert provider.call_count == 0
    assert run_count == 0
