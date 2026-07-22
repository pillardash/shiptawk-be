from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.generation.content_memory import DraftMemoryInput, build_content_memory
from app.domains.generation.context import (
    GenerationContextBuilder,
    ProductContextInput,
    RepositoryContextInput,
    SafetySettingsInput,
    UserContextInput,
    VoiceProfileInput,
)
from app.domains.generation.provider import GenerationProvider
from app.domains.generation.repository import GenerationRepository
from app.domains.generation.service import GenerationInput, GenerationOrchestrator
from app.domains.integrations.event_normalizer import NormalizedEvent
from app.domains.legacy.models import (
    drafts,
    product_repositories,
)
from app.domains.products.models import Product
from app.domains.users.models import User
from app.workflows.github import GitHubWorkflowContext


class DatabaseGenerationWorkflow:
    """Builds database-owned generation context and invokes the idempotent domain service."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        provider: GenerationProvider,
        model: str,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._model = model

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
        user = await db.scalar(
            select(User).where(User.id == context.user_id, User.is_active.is_(True))
        )
        if user is None:
            raise RuntimeError("Generation user is unavailable.")
        product_row = (
            await db.execute(
                select(
                    Product,
                    product_repositories.c.repo_role,
                    product_repositories.c.is_primary_repo,
                    product_repositories.c.generated_role_description,
                    product_repositories.c.role_description_override,
                )
                .join(
                    product_repositories,
                    product_repositories.c.product_id == Product.id,
                )
                .where(
                    Product.workspace_id == context.workspace_id,
                    product_repositories.c.workspace_id == context.workspace_id,
                    product_repositories.c.repo_id == context.repo_id,
                )
                .limit(1)
            )
        ).one_or_none()
        product = product_row[0] if product_row is not None else None
        generation_context = GenerationContextBuilder().build(
            user=UserContextInput(
                tone_preference=user.tone_preference,
                voice_profile=VoiceProfileInput.model_validate(user.voice_profile),
                safety_settings=SafetySettingsInput.model_validate(user.safety_settings),
            ),
            repo=RepositoryContextInput(
                repo_name=context.repo_name,
                repo_full_name=context.repo_full_name,
                description=context.repo_description,
            ),
            style_anchors=[],
            product=self._product_input(product) if product is not None else None,
            repo_role=product_row.repo_role if product_row is not None else None,
            is_primary_repo=product_row.is_primary_repo if product_row is not None else None,
            repo_role_description=(
                product_row.role_description_override or product_row.generated_role_description
                if product_row is not None
                else None
            ),
        )
        week_ago = datetime.now(UTC) - timedelta(days=7)
        drafts_this_week = int(
            await db.scalar(
                select(func.count())
                .select_from(drafts)
                .where(
                    drafts.c.workspace_id == context.workspace_id,
                    drafts.c.user_id == context.user_id,
                    drafts.c.created_at >= week_ago,
                )
            )
            or 0
        )
        memory_rows = (
            await db.execute(
                select(drafts.c.content, drafts.c.source_event_payload)
                .where(
                    drafts.c.workspace_id == context.workspace_id,
                    drafts.c.user_id == context.user_id,
                    drafts.c.status.in_(["approved", "posted"]),
                )
                .order_by(drafts.c.created_at.desc())
                .limit(20)
            )
        ).all()
        stable_key = f"github-consumer:{context.consumer_id}"
        result = await GenerationOrchestrator(
            repository=GenerationRepository(db),
            provider=self._provider,
            model=self._model,
        ).run(
            GenerationInput(
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                repo_id=context.repo_id,
                product_id=product.id if product is not None else None,
                normalized_event_id=normalized_event_id,
                stable_key=stable_key,
                correlation_id=str(context.consumer_id),
                event=event,
                context=generation_context,
                repo_private=context.repo_private,
                drafts_created_this_week=drafts_this_week,
                content_memory=build_content_memory(
                    [
                        DraftMemoryInput(
                            content=row.content,
                            source_event_payload=row.source_event_payload,
                        )
                        for row in memory_rows
                    ]
                ),
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

    @staticmethod
    def _product_input(product: Product) -> ProductContextInput:
        return ProductContextInput(
            name=product.name,
            description=product.description,
            target_audience=product.target_audience,
            messaging_angle=product.messaging_angle,
            tone_override=product.tone_override,
            blocked_terms=product.blocked_terms,
            blocked_topics=product.blocked_topics,
            safe_public_boundaries=product.safe_public_boundaries,
            primary_customer_pain=product.primary_customer_pain,
            desired_outcome=product.desired_outcome,
            positioning_statement=product.positioning_statement,
            proof_points=product.proof_points,
            customer_use_cases=product.customer_use_cases,
            content_goal=product.content_goal,
            cta_preference=product.cta_preference,
            founder_story_angle=product.founder_story_angle,
        )
