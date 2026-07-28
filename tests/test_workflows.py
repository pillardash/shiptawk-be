import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, cast
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import inngest
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.main import create_app
from app.modules.drafts.models import drafts
from app.modules.drafts.services.event_to_draft_service import EventToDraftService
from app.modules.identity.models.users import User
from app.modules.integrations.events import NormalizedEvent
from app.modules.integrations.models import (
    github_raw_event_consumers,
    normalized_events,
)
from app.modules.products.models import Product, product_repositories
from app.modules.repos.models import repos
from app.modules.workspaces.models import Workspace
from app.services.github_webhook import (
    GitHubWebhookEnvelope,
    ingest_github_webhook,
)
from app.workflows.contracts import (
    GitHubEventMetadata,
    OnboardingEventMetadata,
    SearchSyncEventMetadata,
)
from app.workflows.generation import DatabaseGenerationWorkflow
from app.workflows.github import GitHubConsumerWorkflow, GitHubWorkflowContext
from app.workflows.publisher import MetadataEvent, publish_pending_consumers
from app.workflows.runtime import (
    DeferredOnboardingWorkflow,
    create_workflow_functions,
    serve_workflow_functions,
)
from app.workflows.unavailable import UnavailableGitHubConsumerWorkflow
from tests.test_generation_orchestration import central_llm


@pytest.fixture
async def workflow_db() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield sessions
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def seed_consumer(sessions: async_sessionmaker[AsyncSession], encryption_key: str) -> UUID:
    async with sessions() as db:
        user = User(
            email=f"workflow-{uuid4()}@example.com",
            voice_profile={
                "tone_modes": ["concise"],
                "phrases_to_avoid": [],
                "audience_preference": "Product teams",
                "style_samples": [],
            },
            safety_settings={
                "blocked_terms": [],
                "blocked_topics": [],
                "ignored_paths": [],
                "preferred_event_types": ["push", "release", "pull_request_merged"],
                "max_drafts_per_week": 5,
                "private_repo_mode": "normal",
            },
        )
        workspace = Workspace(name="Workflow workspace")
        db.add_all([user, workspace])
        await db.flush()
        await db.execute(
            insert(repos).values(
                id=uuid4(),
                workspace_id=workspace.id,
                user_id=user.id,
                repo_name="api",
                repo_full_name="acme/api",
                tracked_branch="main",
                is_tracked=True,
            )
        )
        result = await ingest_github_webhook(
            db,
            envelope=GitHubWebhookEnvelope(
                delivery_id=f"delivery-{uuid4()}",
                event_name="push",
                repo_full_name="acme/api",
            ),
            raw_body=json.dumps(
                {
                    "repository": {"full_name": "acme/api"},
                    "ref": "refs/heads/main",
                    "commits": [
                        {
                            "id": "abc123",
                            "message": "Ship durable workflows",
                            "url": "https://github.com/acme/api/commit/abc123",
                            "timestamp": "2026-07-22T10:00:00Z",
                        }
                    ],
                }
            ).encode(),
            encryption_key=encryption_key,
        )
        consumer_id = await db.scalar(
            select(github_raw_event_consumers.c.id).where(
                github_raw_event_consumers.c.raw_event_id == result.raw_event_id
            )
        )
        assert consumer_id is not None
        return cast(UUID, consumer_id)


@dataclass
class FakeGenerationWorkflow:
    failures_remaining: int = 0
    calls: list[tuple[GitHubWorkflowContext, NormalizedEvent, UUID]] = field(default_factory=list)

    async def generate(
        self,
        context: GitHubWorkflowContext,
        event: NormalizedEvent,
        normalized_event_id: UUID,
    ) -> None:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("transient generation failure")
        self.calls.append((context, event, normalized_event_id))


@pytest.mark.asyncio
async def test_consumer_workflow_is_idempotent_and_keeps_payload_out_of_event_metadata(
    workflow_db: async_sessionmaker[AsyncSession],
) -> None:
    key = Fernet.generate_key().decode()
    consumer_id = await seed_consumer(workflow_db, key)
    generation = FakeGenerationWorkflow()
    workflow = GitHubConsumerWorkflow(
        sessions=workflow_db, encryption_key=key, generation=generation
    )

    first = await workflow.process(consumer_id)
    second = await workflow.process(consumer_id)

    assert first == {"status": "processed", "generated": True}
    assert second == {"status": "processed", "generated": False}
    assert len(generation.calls) == 1
    assert generation.calls[0][1].title == "Ship durable workflows"
    async with workflow_db() as db:
        assert await db.scalar(select(func.count()).select_from(normalized_events)) == 1
        state = await db.scalar(
            select(github_raw_event_consumers.c.processing_state).where(
                github_raw_event_consumers.c.id == consumer_id
            )
        )
    assert state == "processed"


@pytest.mark.asyncio
async def test_consumer_workflow_retries_a_failed_attempt_without_duplicate_normalization(
    workflow_db: async_sessionmaker[AsyncSession],
) -> None:
    key = Fernet.generate_key().decode()
    consumer_id = await seed_consumer(workflow_db, key)
    generation = FakeGenerationWorkflow(failures_remaining=1)
    workflow = GitHubConsumerWorkflow(
        sessions=workflow_db, encryption_key=key, generation=generation
    )

    with pytest.raises(RuntimeError, match="transient generation failure"):
        await workflow.process(consumer_id)
    result = await workflow.process(consumer_id)

    assert result == {"status": "processed", "generated": True}
    assert len(generation.calls) == 1
    async with workflow_db() as db:
        assert await db.scalar(select(func.count()).select_from(normalized_events)) == 1


@pytest.mark.asyncio
async def test_database_generation_workflow_builds_product_context_and_persists_selected_draft(
    workflow_db: async_sessionmaker[AsyncSession],
) -> None:
    key = Fernet.generate_key().decode()
    consumer_id = await seed_consumer(workflow_db, key)
    async with workflow_db() as db:
        consumer = (
            await db.execute(
                select(
                    github_raw_event_consumers.c.workspace_id,
                    github_raw_event_consumers.c.user_id,
                    github_raw_event_consumers.c.repo_id,
                ).where(github_raw_event_consumers.c.id == consumer_id)
            )
        ).one()
        product = Product(
            workspace_id=consumer.workspace_id,
            user_id=consumer.user_id,
            name="Shiptawk",
            description="Evidence-backed marketing.",
            target_audience="Product teams",
            messaging_angle="Lead with shipped value.",
        )
        db.add(product)
        await db.flush()
        await db.execute(
            insert(product_repositories).values(
                id=uuid4(),
                workspace_id=consumer.workspace_id,
                product_id=product.id,
                repo_id=consumer.repo_id,
                repo_role="backend",
                is_primary_repo=True,
                generated_role_description="Owns the API.",
            )
        )
        await db.commit()

    llm, provider = central_llm(
        workflow_db,
        {
            "candidates": [
                {
                    "content": "Product teams can turn shipped changes into reviewed marketing.",
                    "variant": "customer_value",
                    "angle": "user_benefit",
                    "rationale": "Connects a release to a customer outcome.",
                    "evidenceIds": ["event.change.1"],
                }
            ]
        },
    )
    generation = DatabaseGenerationWorkflow(EventToDraftService(sessions=workflow_db, llm=llm))
    context = GitHubWorkflowContext(
        consumer_id=consumer_id,
        raw_event_id=uuid4(),
        workspace_id=consumer.workspace_id,
        user_id=consumer.user_id,
        repo_id=consumer.repo_id,
        repo_name="api",
        repo_full_name="acme/api",
        repo_private=False,
        repo_description="Public product API",
        tracked_branch="main",
        event_name="release",
        payload={},
    )
    event = NormalizedEvent(
        event_type="release",
        repo_full_name="acme/api",
        title="Shiptawk release v2",
        description="Product teams can turn release updates into reviewed marketing posts.",
        url="https://github.com/acme/api/releases/v2",
        occurred_at="2026-07-22T10:00:00Z",
        sha=None,
        branch_name=None,
        touched_paths=[],
        dedupe_key="release:acme/api:v2",
    )

    await generation.generate(context, event, uuid4())
    await generation.generate(context, event, uuid4())
    async with workflow_db() as db:
        draft = (await db.execute(select(drafts))).mappings().one()
    assert draft["content"] == "Product teams can turn shipped changes into reviewed marketing."
    assert draft["status"] == "pending"
    assert draft["source_event_payload"]["selectedAngle"] == "user_benefit"
    assert len(provider.requests) == 1


@dataclass
class FakePublisher:
    events: list[MetadataEvent] = field(default_factory=list)
    failures_remaining: int = 0

    async def publish(self, event: MetadataEvent) -> None:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("event transport unavailable")
        self.events.append(event)


@pytest.mark.asyncio
async def test_pending_consumer_publication_uses_stable_metadata_only_event(
    workflow_db: async_sessionmaker[AsyncSession],
) -> None:
    key = Fernet.generate_key().decode()
    consumer_id = await seed_consumer(workflow_db, key)
    publisher = FakePublisher()

    async with workflow_db() as db:
        await publish_pending_consumers(db, publisher=publisher, raw_event_id=None)

    assert publisher.events == [
        MetadataEvent(
            name="github/event.received",
            event_id=str(consumer_id),
            data={
                "consumerId": str(consumer_id),
                "schemaVersion": 1,
                "correlationId": str(consumer_id),
            },
        )
    ]
    assert "payload" not in publisher.events[0].data


def test_workflow_contracts_reject_payloads_and_require_stable_metadata() -> None:
    consumer_id = uuid4()
    assert (
        GitHubEventMetadata.model_validate(
            {
                "consumerId": consumer_id,
                "schemaVersion": 1,
                "correlationId": consumer_id,
            }
        ).consumer_id
        == consumer_id
    )
    with pytest.raises(ValidationError):
        GitHubEventMetadata.model_validate(
            {
                "consumerId": consumer_id,
                "schemaVersion": 1,
                "correlationId": consumer_id,
                "payload": {"private": True},
            }
        )
    with pytest.raises(ValidationError):
        OnboardingEventMetadata.model_validate(
            {"userId": uuid4(), "workspaceId": uuid4(), "schemaVersion": 2}
        )
    run_id = uuid4()
    assert (
        SearchSyncEventMetadata.model_validate(
            {
                "runId": run_id,
                "workspaceId": uuid4(),
                "productId": uuid4(),
                "sourceId": uuid4(),
                "schemaVersion": 1,
                "correlationId": run_id,
            }
        ).run_id
        == run_id
    )
    with pytest.raises(ValidationError):
        SearchSyncEventMetadata.model_validate(
            {
                "runId": run_id,
                "workspaceId": uuid4(),
                "productId": uuid4(),
                "sourceId": uuid4(),
                "schemaVersion": 1,
                "correlationId": run_id,
                "query": "private query",
            }
        )


@pytest.mark.asyncio
async def test_search_sync_workflow_validates_correlation_and_passes_only_run_id() -> None:
    @dataclass
    class FakeSearchSync:
        calls: list[UUID] = field(default_factory=list)

        async def process(self, run_id: UUID) -> dict[str, object]:
            self.calls.append(run_id)
            return {"status": "succeeded"}

    search = FakeSearchSync()
    functions = create_workflow_functions(
        client=inngest.Inngest(app_id="search-sync-workflow-test"),
        github=UnavailableGitHubConsumerWorkflow(),
        onboarding=DeferredOnboardingWorkflow(),
        digests=None,
        search_syncs=search,
    ).functions
    run_id = uuid4()
    context = MagicMock()
    context.event.data = {
        "runId": str(run_id),
        "workspaceId": str(uuid4()),
        "productId": str(uuid4()),
        "sourceId": str(uuid4()),
        "schemaVersion": 1,
        "correlationId": str(run_id),
    }

    assert await cast(Any, functions[9])._handler(context) == {"status": "succeeded"}
    assert search.calls == [run_id]
    context.event.data["correlationId"] = str(uuid4())
    with pytest.raises(ValueError, match="correlation metadata"):
        await cast(Any, functions[9])._handler(context)


@pytest.mark.asyncio
async def test_failed_event_publication_can_be_retried_without_a_duplicate_consumer(
    workflow_db: async_sessionmaker[AsyncSession],
) -> None:
    key = Fernet.generate_key().decode()
    consumer_id = await seed_consumer(workflow_db, key)
    publisher = FakePublisher(failures_remaining=1)

    async with workflow_db() as db:
        with pytest.raises(RuntimeError, match="event transport unavailable"):
            await publish_pending_consumers(db, publisher=publisher, raw_event_id=None)
    async with workflow_db() as db:
        await publish_pending_consumers(db, publisher=publisher, raw_event_id=None)
        row = (
            await db.execute(
                select(
                    github_raw_event_consumers.c.processing_state,
                    github_raw_event_consumers.c.publish_attempts,
                ).where(github_raw_event_consumers.c.id == consumer_id)
            )
        ).one()

    assert len(publisher.events) == 1
    assert (row.processing_state, row.publish_attempts) == ("enqueued", 2)


def test_fastapi_serves_all_backend_owned_inngest_functions() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/inngest")

    assert response.status_code == 200
    assert response.json()["function_count"] == 16
    assert response.json()["mode"] == "dev"


@pytest.mark.asyncio
async def test_digest_schedules_use_deterministic_backend_workflows() -> None:
    @dataclass
    class FakeDigestWorkflow:
        calls: list[str] = field(default_factory=list)

        async def deliver(self, frequency: str) -> Any:
            self.calls.append(frequency)
            return type("Result", (), {"sent": 1, "skipped": 2})()

    @dataclass
    class FakeDraftDigestWorkflow:
        calls: int = 0

        async def deliver(self) -> Any:
            self.calls += 1
            return type("Result", (), {"sent": 3, "skipped": 4})()

    achievement = FakeDigestWorkflow()
    repository = FakeDigestWorkflow()
    drafts = FakeDraftDigestWorkflow()
    functions = create_workflow_functions(
        client=inngest.Inngest(app_id="workflow-test"),
        github=UnavailableGitHubConsumerWorkflow(),
        onboarding=DeferredOnboardingWorkflow(),
        digests=achievement,
        draft_digests=drafts,
        repo_digests=repository,
    ).functions
    context = MagicMock()

    results = [await cast(Any, function)._handler(context) for function in functions[3:9]]

    assert results == [
        {"sent": 3, "skipped": 4},
        {"sent": 1, "skipped": 2},
        {"sent": 1, "skipped": 2},
        {"sent": 1, "skipped": 2},
        {"sent": 1, "skipped": 2},
        {"claimed": 0, "published": 0, "retried": 0, "failed": 0},
    ]
    assert drafts.calls == 1
    assert achievement.calls == ["weekly", "monthly"]
    assert repository.calls == ["weekly", "monthly"]


@pytest.mark.asyncio
async def test_workflow_handlers_validate_metadata_and_skip_unconfigured_digest_workflows() -> None:
    @dataclass
    class FakeGitHubWorkflow:
        calls: list[UUID] = field(default_factory=list)

        async def process(self, consumer_id: UUID) -> dict[str, object]:
            self.calls.append(consumer_id)
            return {"status": "processed"}

    @dataclass
    class FakeOnboardingWorkflow:
        calls: list[tuple[UUID, UUID]] = field(default_factory=list)

        async def generate(self, user_id: UUID, workspace_id: UUID) -> dict[str, object]:
            self.calls.append((user_id, workspace_id))
            return {"status": "generated"}

    github = FakeGitHubWorkflow()
    onboarding = FakeOnboardingWorkflow()
    functions = create_workflow_functions(
        client=inngest.Inngest(app_id="workflow-handler-test"),
        github=github,
        onboarding=onboarding,
        digests=None,
    ).functions
    consumer_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    github_context = MagicMock()
    github_context.event.data = {
        "consumerId": str(consumer_id),
        "schemaVersion": 1,
        "correlationId": str(consumer_id),
    }
    onboarding_context = MagicMock()
    onboarding_context.event.data = {
        "userId": str(user_id),
        "workspaceId": str(workspace_id),
        "schemaVersion": 1,
    }

    assert await cast(Any, functions[0])._handler(github_context) == {"status": "processed"}
    assert await cast(Any, functions[1])._handler(onboarding_context) == {"status": "generated"}
    website_context = MagicMock()
    run_id = uuid4()
    website_context.event.data = {
        "runId": str(run_id),
        "workspaceId": str(workspace_id),
        "productId": str(uuid4()),
        "sourceId": str(uuid4()),
        "schemaVersion": 1,
        "correlationId": str(run_id),
    }
    assert await cast(Any, functions[2])._handler(website_context) == {
        "status": "skipped",
        "reason": "website crawl workflow is not configured",
        "runId": str(run_id),
    }
    assert [await cast(Any, function)._handler(MagicMock()) for function in functions[3:9]] == [
        {"sent": 0, "skipped": 0},
        {"sent": 0, "skipped": 0},
        {"sent": 0, "skipped": 0},
        {"sent": 0, "skipped": 0},
        {"sent": 0, "skipped": 0},
        {"claimed": 0, "published": 0, "retried": 0, "failed": 0},
    ]
    assert github.calls == [consumer_id]
    assert onboarding.calls == [(user_id, workspace_id)]

    github_context.event.data["correlationId"] = str(uuid4())
    with pytest.raises(ValueError, match="correlation metadata"):
        await cast(Any, functions[0])._handler(github_context)


@pytest.mark.asyncio
async def test_deferred_onboarding_includes_only_stable_metadata() -> None:
    user_id = uuid4()
    workspace_id = uuid4()

    result = await DeferredOnboardingWorkflow().generate(user_id, workspace_id)

    assert result == {
        "status": "skipped",
        "reason": "source events generate drafts after repository opt-in",
        "userId": str(user_id),
        "workspaceId": str(workspace_id),
    }


@pytest.mark.asyncio
async def test_website_crawl_workflow_dispatches_only_the_run_id() -> None:
    @dataclass
    class FakeWebsiteCrawlWorkflow:
        calls: list[UUID] = field(default_factory=list)

        async def process(self, run_id: UUID) -> dict[str, object]:
            self.calls.append(run_id)
            return {"status": "succeeded"}

    website = FakeWebsiteCrawlWorkflow()
    functions = create_workflow_functions(
        client=inngest.Inngest(app_id="website-workflow-test"),
        github=UnavailableGitHubConsumerWorkflow(),
        onboarding=DeferredOnboardingWorkflow(),
        digests=None,
        website_crawls=website,
    ).functions
    run_id = uuid4()
    context = MagicMock()
    context.event.data = {
        "runId": str(run_id),
        "workspaceId": str(uuid4()),
        "productId": str(uuid4()),
        "sourceId": str(uuid4()),
        "schemaVersion": 1,
        "correlationId": str(run_id),
    }

    assert await cast(Any, functions[2])._handler(context) == {"status": "succeeded"}
    assert website.calls == [run_id]


def test_serve_workflow_functions_registers_the_inngest_route() -> None:
    app = FastAPI()
    workflow = create_workflow_functions(
        client=inngest.Inngest(app_id="workflow-serve-test"),
        github=UnavailableGitHubConsumerWorkflow(),
        onboarding=DeferredOnboardingWorkflow(),
        digests=None,
    )

    with patch("app.workflows.runtime.inngest.fast_api.serve") as serve:
        serve_workflow_functions(app, workflow)

    serve.assert_called_once_with(
        app,
        workflow.client,
        cast(list[Any], workflow.functions),
        serve_path="/api/inngest",
    )
