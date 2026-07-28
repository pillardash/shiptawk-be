from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.drafts.detectors.repetition_detector import build_content_memory
from app.modules.drafts.models import drafts
from app.modules.drafts.schemas.context import (
    EffectiveGenerationContext,
    ProductContextInput,
    SafetySettingsInput,
    UserContextInput,
    VoiceProfileInput,
)
from app.modules.drafts.schemas.memory import ContentMemory, DraftMemoryInput
from app.modules.drafts.services.generation_context_service import GenerationContextBuilder
from app.modules.identity.models.users import User
from app.modules.products.models import Product, product_repositories
from app.modules.repos.schemas.generation_context_schema import RepositoryContextInput


class RepositoryGenerationContext:
    def __init__(
        self,
        *,
        product_id: UUID,
        context: EffectiveGenerationContext,
        drafts_created_this_week: int,
        content_memory: ContentMemory,
    ) -> None:
        self.product_id = product_id
        self.context = context
        self.drafts_created_this_week = drafts_created_this_week
        self.content_memory = content_memory


async def load_event_generation_context(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    repo_id: UUID,
    repo: RepositoryContextInput,
) -> RepositoryGenerationContext:
    user = await db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    if user is None:
        raise RuntimeError("Generation user is unavailable.")
    row = (
        await db.execute(
            select(
                Product,
                product_repositories.c.repo_role,
                product_repositories.c.is_primary_repo,
                product_repositories.c.generated_role_description,
                product_repositories.c.role_description_override,
            )
            .join(product_repositories, product_repositories.c.product_id == Product.id)
            .where(
                Product.workspace_id == workspace_id,
                product_repositories.c.workspace_id == workspace_id,
                product_repositories.c.repo_id == repo_id,
            )
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        raise RuntimeError("Generation repository has no product context.")
    product = row[0]
    context = GenerationContextBuilder().build(
        user=UserContextInput(
            tone_preference=user.tone_preference,
            voice_profile=VoiceProfileInput.model_validate(user.voice_profile),
            safety_settings=SafetySettingsInput.model_validate(user.safety_settings),
        ),
        repo=repo,
        style_anchors=[],
        product=_product_input(product),
        repo_role=row.repo_role,
        is_primary_repo=row.is_primary_repo,
        repo_role_description=row.role_description_override or row.generated_role_description,
    )
    week_ago = datetime.now(UTC) - timedelta(days=7)
    weekly = int(
        await db.scalar(
            select(func.count())
            .select_from(drafts)
            .where(
                drafts.c.workspace_id == workspace_id,
                drafts.c.user_id == user_id,
                drafts.c.created_at >= week_ago,
            )
        )
        or 0
    )
    memory_rows = (
        await db.execute(
            select(drafts.c.content, drafts.c.source_event_payload)
            .where(
                drafts.c.workspace_id == workspace_id,
                drafts.c.user_id == user_id,
                drafts.c.status.in_(["approved", "posted"]),
            )
            .order_by(drafts.c.created_at.desc())
            .limit(20)
        )
    ).all()
    return RepositoryGenerationContext(
        product_id=product.id,
        context=context,
        drafts_created_this_week=weekly,
        content_memory=build_content_memory(
            [
                DraftMemoryInput(
                    content=item.content,
                    source_event_payload=item.source_event_payload,
                )
                for item in memory_rows
            ]
        ),
    )


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
