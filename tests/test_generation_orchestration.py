from collections.abc import AsyncGenerator
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import StaticPool, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.modules.drafts.models import tweet_candidates
from app.modules.drafts.repositories.tweet_candidate_repository import TweetCandidateRepository
from app.modules.drafts.schemas.context import (
    ProductContextInput,
    SafetySettingsInput,
    UserContextInput,
    VoiceProfileInput,
)
from app.modules.drafts.schemas.orchestration import GenerationInput
from app.modules.drafts.services.generation_context_service import GenerationContextBuilder
from app.modules.drafts.services.generation_service import (
    InvalidGenerationOutput,
    TweetGenerationService,
)
from app.modules.integrations.events import NormalizedEvent
from app.modules.llm.models import generation_runs
from app.modules.llm.policies.llm_route_policy import (
    LLMModelRegistration,
    LLMRoutePlan,
    LLMRoutePolicy,
    LLMRouteStep,
    ModelPricing,
)
from app.modules.llm.providers.fake.fake_llm_provider import FakeLLMProvider
from app.modules.llm.providers.generation_result import LLMGenerationResult, LLMGenerationTelemetry
from app.modules.llm.providers.provider_registry import LLMProviderRegistry
from app.modules.llm.repositories.generation_run_repository import GenerationRunRepository
from app.modules.llm.services.llm_execution_service import LLMExecutionService
from app.modules.repos.schemas.generation_context_schema import RepositoryContextInput


@pytest.fixture
async def sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


def generation_input() -> GenerationInput:
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
    return GenerationInput(
        workspace_id=uuid4(),
        user_id=uuid4(),
        repo_id=uuid4(),
        product_id=uuid4(),
        normalized_event_id=uuid4(),
        stable_key="github-consumer:42",
        correlation_id="42",
        event=NormalizedEvent(
            event_type="release",
            repo_full_name="acme/shiptawk",
            title="Shiptawk release v2",
            description="Product teams can now turn release updates into marketing posts.",
            url="https://github.com/acme/shiptawk/releases/v2",
            occurred_at="2026-07-22T12:00:00Z",
            sha=None,
            branch_name=None,
            touched_paths=[],
            dedupe_key="release|acme/shiptawk|v2",
        ),
        context=context,
        repo_private=False,
        drafts_created_this_week=0,
    )


def central_llm(
    sessions: async_sessionmaker[AsyncSession], output: object
) -> tuple[LLMExecutionService, FakeLLMProvider]:
    provider = FakeLLMProvider(
        LLMGenerationResult(
            output=output,
            telemetry=LLMGenerationTelemetry(
                provider="fake", model="fake-v1", correlation_id="provider-correlation"
            ),
        )
    )
    registration = LLMModelRegistration(
        name="tweet-model",
        provider="fake",
        model="fake-v1",
        capabilities={"structured_output"},
        pricing=ModelPricing(input_per_million=Decimal(0), output_per_million=Decimal(0)),
        pricing_version="test",
    )
    routes = LLMRoutePolicy(
        {registration.name: registration},
        {
            "tweet-candidates": LLMRoutePlan(
                name="tweet-candidates",
                steps=(LLMRouteStep(model=registration.name),),
                max_attempts=1,
            )
        },
    )
    return LLMExecutionService(sessions, LLMProviderRegistry({"fake": provider}), routes), provider


def valid_output() -> dict[str, object]:
    return {
        "candidates": [
            {
                "content": (
                    "Shiptawk now helps product teams turn release updates into marketing posts."
                ),
                "variant": "customer_value",
                "angle": "user_benefit",
                "rationale": "Leads with the supported user benefit.",
                "evidenceIds": ["event.change.2", "product.identity.1"],
            }
        ]
    }


@pytest.mark.asyncio
async def test_domain_rollback_replays_validated_snapshot_without_provider_call(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payload = generation_input()
    llm, provider = central_llm(sessions, valid_output())

    async with sessions() as db:
        first = await TweetGenerationService(
            runs=GenerationRunRepository(db),
            candidates=TweetCandidateRepository(db),
            llm=llm,
        ).run(payload)
        assert first.run_id is not None
        await db.rollback()

    async with sessions() as db:
        second = await TweetGenerationService(
            runs=GenerationRunRepository(db),
            candidates=TweetCandidateRepository(db),
            llm=llm,
        ).run(payload)
        await db.commit()
        run_count = len((await db.execute(select(generation_runs))).all())
        candidate_count = len((await db.execute(select(tweet_candidates))).all())
        assert second.run_id is not None
        assert await GenerationRunRepository(db).get(uuid4(), second.run_id) is None

    assert second.reused is True
    assert len(provider.requests) == 1
    assert run_count == 1
    assert candidate_count == 1


@pytest.mark.asyncio
async def test_invalid_output_is_controlled_and_never_persisted(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payload = generation_input()
    llm, _ = central_llm(sessions, {"candidates": [{"content": "invalid"}]})
    async with sessions() as db:
        with pytest.raises(InvalidGenerationOutput):
            await TweetGenerationService(
                runs=GenerationRunRepository(db),
                candidates=TweetCandidateRepository(db),
                llm=llm,
            ).run(payload)
        assert len((await db.execute(select(generation_runs))).all()) == 0


@pytest.mark.asyncio
async def test_domain_no_generation_does_not_call_llm(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    payload = generation_input().model_copy(
        update={
            "event": generation_input().event.model_copy(
                update={"title": "Secret token rotation", "description": "Internal security fix"}
            )
        }
    )
    llm, provider = central_llm(sessions, valid_output())
    async with sessions() as db:
        result = await TweetGenerationService(
            runs=GenerationRunRepository(db),
            candidates=TweetCandidateRepository(db),
            llm=llm,
        ).run(payload)

    assert result.outcome == "block"
    assert provider.requests == []
