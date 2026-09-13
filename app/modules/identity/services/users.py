import logging
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.enums import OnboardingStep
from app.modules.identity.events.onboarding import OnboardingGenerationRequest
from app.modules.identity.models.oauth import AuthSession, OAuthIdentity
from app.modules.identity.models.users import User
from app.modules.identity.providers.onboarding import OnboardingGenerationTrigger
from app.modules.identity.repositories.users import get_user_by_id
from app.modules.identity.schemas.users import (
    UserGenerationSettingsResponse,
    UserGenerationSettingsUpdate,
    UserOnboardingStatusResponse,
    UserProfileResponse,
    UserSafetySettings,
    UserVoiceProfile,
)
from app.modules.integrations.models import IntegrationConnection
from app.modules.products.enums.product_profile_enum import ProductProfileStatus
from app.modules.products.models import ProductProfile, product_repositories
from app.modules.repos.models import repos
from app.modules.workspaces.models import Workspace
from app.modules.workspaces.repositories import has_active_membership
from app.shared.exceptions import ConflictError, ForbiddenError, UnauthorizedError

logger = logging.getLogger(__name__)

DEFAULT_VOICE_PROFILE = {
    "tone_modes": ["concise", "direct"],
    "phrases_to_avoid": [],
    "audience_preference": "",
    "style_samples": [],
}
DEFAULT_SAFETY_SETTINGS = {
    "blocked_terms": [],
    "blocked_topics": [],
    "ignored_paths": [],
    "preferred_event_types": ["push", "release", "pull_request_merged"],
    "max_drafts_per_week": 5,
    "private_repo_mode": "normal",
}


async def get_active_user_by_id(db: AsyncSession, user_id: UUID) -> User:
    user = await get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("Invalid authentication credentials.", code="invalid_token")
    if not user.is_active:
        raise UnauthorizedError("User account is inactive.", code="inactive_user")
    return user


def get_generation_settings(user: User) -> UserGenerationSettingsResponse:
    try:
        voice_profile = UserVoiceProfile.model_validate(user.voice_profile)
    except ValidationError:
        voice_profile = UserVoiceProfile.model_validate(DEFAULT_VOICE_PROFILE)
    try:
        safety_settings = UserSafetySettings.model_validate(user.safety_settings)
    except ValidationError:
        safety_settings = UserSafetySettings.model_validate(DEFAULT_SAFETY_SETTINGS)
    return UserGenerationSettingsResponse(
        tone_preference=user.tone_preference,
        voice_profile=voice_profile,
        safety_settings=safety_settings,
        email_notifications_enabled=user.email_notifications_enabled,
        achievement_digest_enabled=user.achievement_digest_enabled,
        achievement_digest_frequency=user.achievement_digest_frequency,
        achievement_digest_prompt_dismissed=user.achievement_digest_prompt_dismissed,
        achievement_digest_onboarding_seen=user.achievement_digest_onboarding_seen,
    )


async def update_generation_settings(
    db: AsyncSession, user: User, settings: UserGenerationSettingsUpdate
) -> UserGenerationSettingsResponse:
    user.tone_preference = settings.tone_preference.value
    user.voice_profile = settings.voice_profile.model_dump(mode="json")
    user.safety_settings = settings.safety_settings.model_dump(mode="json")
    for field in (
        "email_notifications_enabled",
        "achievement_digest_enabled",
        "achievement_digest_frequency",
        "achievement_digest_prompt_dismissed",
        "achievement_digest_onboarding_seen",
    ):
        value = getattr(settings, field)
        if value is not None:
            setattr(user, field, value.value if hasattr(value, "value") else value)
    try:
        await db.commit()
        await db.refresh(user)
    except Exception:
        await db.rollback()
        raise
    return get_generation_settings(user)


async def get_user_profile(db: AsyncSession, user: User) -> UserProfileResponse:
    identity = await db.scalar(
        select(OAuthIdentity)
        .where(
            OAuthIdentity.user_id == user.id,
            OAuthIdentity.provider == "github",
            OAuthIdentity.deleted_at.is_(None),
        )
        .order_by(OAuthIdentity.updated_at.desc())
        .limit(1)
    )
    return UserProfileResponse(
        id=user.id,
        email=user.email,
        github_username=identity.username if identity else user.github_username,
        github_display_name=identity.display_name if identity else None,
        github_avatar_url=identity.avatar_url if identity else None,
        onboarding_completed=user.onboarding_completed,
    )


async def get_session_workspace_id(db: AsyncSession, *, user_id: UUID, session_id: UUID) -> UUID:
    workspace_id = await db.scalar(
        select(AuthSession.current_workspace_id).where(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    )
    if workspace_id is None:
        raise UnauthorizedError("Invalid browser session.", code="invalid_session")
    if not await has_active_membership(db, workspace_id, user_id):
        raise ForbiddenError("Workspace access denied.", code="workspace_access_denied")
    return workspace_id


async def get_onboarding_status(
    db: AsyncSession, *, user: User, workspace_id: UUID
) -> UserOnboardingStatusResponse:
    github_connected = bool(
        await db.scalar(
            select(func.count())
            .select_from(IntegrationConnection)
            .where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.provider == "github_app",
                IntegrationConnection.status == "active",
                IntegrationConnection.deleted_at.is_(None),
            )
        )
    )
    repo_rows = (
        await db.execute(
            select(repos.c.id, repos.c.is_tracked).where(
                repos.c.workspace_id == workspace_id,
                repos.c.user_id == user.id,
            )
        )
    ).all()
    tracked_repo_ids = {row.id for row in repo_rows if row.is_tracked}
    mapped_rows = (
        (
            await db.execute(
                select(product_repositories.c.repo_id, product_repositories.c.product_id).where(
                    product_repositories.c.workspace_id == workspace_id,
                    product_repositories.c.repo_id.in_(tracked_repo_ids),
                )
            )
        ).all()
        if tracked_repo_ids
        else []
    )
    mapped_repo_ids = {row.repo_id for row in mapped_rows}
    used_product_ids = {row.product_id for row in mapped_rows}
    approved_profile_product_ids = set(
        (
            await db.scalars(
                select(ProductProfile.product_id).where(
                    ProductProfile.workspace_id == workspace_id,
                    ProductProfile.product_id.in_(used_product_ids),
                    ProductProfile.status == ProductProfileStatus.approved,
                    ProductProfile.version.is_not(None),
                    ProductProfile.deleted_at.is_(None),
                )
            )
        ).all()
        if used_product_ids
        else []
    )
    used_products_have_context = (
        bool(used_product_ids) and approved_profile_product_ids == used_product_ids
    )
    twitter_connected = bool(
        await db.scalar(
            select(func.count())
            .select_from(IntegrationConnection)
            .where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.provider.in_(("x", "twitter")),
                IntegrationConnection.status == "active",
                IntegrationConnection.deleted_at.is_(None),
            )
        )
    )
    repos_synced = bool(repo_rows)
    has_tracked_repos = bool(tracked_repo_ids)
    tracked_repos_assigned = has_tracked_repos and mapped_repo_ids == tracked_repo_ids
    workspace_completed = bool(
        await db.scalar(select(Workspace.onboarding_completed).where(Workspace.id == workspace_id))
    )
    if workspace_completed:
        next_step = OnboardingStep.complete
    elif not github_connected:
        next_step = OnboardingStep.connect_github
    elif not repos_synced:
        next_step = OnboardingStep.sync_repos
    elif not has_tracked_repos:
        next_step = OnboardingStep.track_repos
    elif not tracked_repos_assigned:
        next_step = OnboardingStep.group_products
    elif not used_products_have_context:
        next_step = OnboardingStep.product_context
    else:
        next_step = OnboardingStep.complete
    return UserOnboardingStatusResponse(
        completed=workspace_completed,
        github_connected=github_connected,
        repos_synced=repos_synced,
        has_tracked_repos=has_tracked_repos,
        tracked_repos_assigned=tracked_repos_assigned,
        used_products_have_context=used_products_have_context,
        twitter_connected=twitter_connected,
        next_step=next_step,
    )


async def complete_onboarding(
    db: AsyncSession,
    *,
    user: User,
    workspace_id: UUID,
    trigger: OnboardingGenerationTrigger | None,
) -> UserOnboardingStatusResponse:
    status = await get_onboarding_status(db, user=user, workspace_id=workspace_id)
    if not (status.github_connected and status.repos_synced and status.tracked_repos_assigned):
        raise ConflictError(
            "GitHub, a tracked repository, and product assignment are required.",
            code="onboarding_requirements_not_met",
        )
    if not status.used_products_have_context:
        raise ConflictError(
            "Every product used by a tracked repository requires an approved product profile.",
            code="approved_product_profile_required",
        )
    newly_completed = await db.scalar(
        update(Workspace)
        .where(Workspace.id == workspace_id, Workspace.onboarding_completed.is_(False))
        .values(onboarding_completed=True, updated_at=datetime.now(UTC))
        .returning(Workspace.id)
    )
    if newly_completed is not None:
        user.onboarding_completed = True
    await db.commit()
    if newly_completed is not None and trigger is not None:
        request = OnboardingGenerationRequest(
            user_id=user.id,
            workspace_id=workspace_id,
            event_id=f"onboarding:{user.id}:{workspace_id}",
        )
        try:
            await trigger.trigger(request)
        except Exception as exc:
            logger.warning("failed to enqueue onboarding generation", extra={"error": str(exc)})
    return await get_onboarding_status(db, user=user, workspace_id=workspace_id)


async def record_dashboard_view(db: AsyncSession, user: User) -> None:
    await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(last_dashboard_viewed_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    )
    await db.commit()
