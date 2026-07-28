from uuid import UUID, uuid5

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.drafts.models import drafts
from app.modules.drafts.repositories.tweet_candidate_repository import TweetCandidateRepository
from app.modules.drafts.schemas.orchestration import GenerationInput
from app.modules.drafts.services.generation_service import TweetGenerationService
from app.modules.integrations.events import NormalizedEvent
from app.modules.llm.repositories.generation_run_repository import GenerationRunRepository
from app.modules.llm.services.llm_execution_service import LLMExecutionService
from app.modules.repos.schemas.generation_context_schema import RepositoryContextInput
from app.modules.repos.services.repository_generation_context_service import (
    load_event_generation_context,
)
from app.workflows.github import GitHubWorkflowContext


class EventToDraftService:
    """Creates a draft and all legacy generation records in one domain transaction."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        llm: LLMExecutionService,
    ) -> None:
        self._sessions = sessions
        self._llm = llm

    async def generate(
        self,
        context: GitHubWorkflowContext,
        event: NormalizedEvent,
        normalized_event_id: UUID,
    ) -> None:
        async with self._sessions() as db:
            await self._generate(db, context, event, normalized_event_id)

    async def _generate(
        self,
        db: AsyncSession,
        context: GitHubWorkflowContext,
        event: NormalizedEvent,
        normalized_event_id: UUID,
    ) -> None:
        loaded = await load_event_generation_context(
            db,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            repo_id=context.repo_id,
            repo=RepositoryContextInput(
                repo_name=context.repo_name,
                repo_full_name=context.repo_full_name,
                description=context.repo_description,
            ),
        )
        stable_key = f"github-consumer:{context.consumer_id}"
        result = await TweetGenerationService(
            runs=GenerationRunRepository(db),
            candidates=TweetCandidateRepository(db),
            llm=self._llm,
        ).run(
            GenerationInput(
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                repo_id=context.repo_id,
                product_id=loaded.product_id,
                normalized_event_id=normalized_event_id,
                stable_key=stable_key,
                correlation_id=str(context.consumer_id),
                event=event,
                context=loaded.context,
                repo_private=context.repo_private,
                drafts_created_this_week=loaded.drafts_created_this_week,
                content_memory=loaded.content_memory,
            )
        )
        if result.run_id is None or not result.candidates:
            return
        selected = result.candidates[0]
        selected_candidate_id = uuid5(result.run_id, f"{selected.rank}:{selected.content}")
        draft_id = uuid5(result.run_id, "selected-draft")
        if await db.scalar(
            select(drafts.c.id).where(
                drafts.c.workspace_id == context.workspace_id,
                drafts.c.id == draft_id,
            )
        ):
            return
        await db.execute(
            insert(drafts).values(
                id=draft_id,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                repo_id=context.repo_id,
                content=selected.content,
                source_event_type=event.event_type,
                source_event_payload={
                    "schemaVersion": 1,
                    "repoFullName": event.repo_full_name,
                    "eventUrl": event.url,
                    "selectedAngle": selected.angle,
                    "evidenceIds": selected.evidence_ids,
                },
                status="pending",
                generation_run_id=result.run_id,
                selected_candidate_id=selected_candidate_id,
                normalized_event_id=normalized_event_id,
            )
        )
        await db.commit()
