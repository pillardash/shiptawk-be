from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import EmailStr, Field, StringConstraints, model_validator

from app.shared.schemas import ApiSchema


class UserResponse(ApiSchema):
    id: UUID
    email: EmailStr | None
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class UserProfileResponse(ApiSchema):
    id: UUID
    email: EmailStr | None
    github_username: str | None
    github_display_name: str | None
    github_avatar_url: str | None
    onboarding_completed: bool


class OnboardingStep(StrEnum):
    connect_github = "connect_github"
    sync_repos = "sync_repos"
    track_repos = "track_repos"
    group_products = "group_products"
    product_context = "product_context"
    review_defaults = "review_defaults"
    complete = "complete"


class UserOnboardingStatusResponse(ApiSchema):
    completed: bool
    github_connected: bool
    repos_synced: bool
    has_tracked_repos: bool
    tracked_repos_assigned: bool
    used_products_have_context: bool
    twitter_connected: bool
    next_step: OnboardingStep


class TonePreference(StrEnum):
    casual = "casual"
    technical = "technical"
    hype = "hype"


class VoiceToneMode(StrEnum):
    direct = "direct"
    technical = "technical"
    playful = "playful"
    concise = "concise"
    founder_style = "founder_style"


class SupportedEventType(StrEnum):
    push = "push"
    release = "release"
    pull_request_merged = "pull_request_merged"


class PrivateRepoMode(StrEnum):
    normal = "normal"
    conservative = "conservative"


class AchievementDigestFrequency(StrEnum):
    weekly = "weekly"
    monthly = "monthly"


Trimmed80 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Trimmed120 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Trimmed180 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
Trimmed300 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
OptionalTrimmed180 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=180)]


class UserVoiceProfile(ApiSchema):
    tone_modes: list[VoiceToneMode] = Field(min_length=1, max_length=5)
    phrases_to_avoid: list[Trimmed80] = Field(max_length=40)
    audience_preference: OptionalTrimmed180
    style_samples: list[Trimmed300] = Field(max_length=12)


class UserSafetySettings(ApiSchema):
    blocked_terms: list[Trimmed80] = Field(max_length=80)
    blocked_topics: list[Trimmed120] = Field(max_length=40)
    ignored_paths: list[Trimmed180] = Field(max_length=80)
    preferred_event_types: list[SupportedEventType] = Field(min_length=1, max_length=3)
    max_drafts_per_week: int = Field(ge=1, le=100)
    private_repo_mode: PrivateRepoMode


class UserGenerationSettingsResponse(ApiSchema):
    tone_preference: TonePreference
    voice_profile: UserVoiceProfile
    safety_settings: UserSafetySettings
    email_notifications_enabled: bool
    achievement_digest_enabled: bool
    achievement_digest_frequency: AchievementDigestFrequency
    achievement_digest_prompt_dismissed: bool
    achievement_digest_onboarding_seen: bool


class UserGenerationSettingsUpdate(ApiSchema):
    tone_preference: TonePreference
    voice_profile: UserVoiceProfile
    safety_settings: UserSafetySettings
    email_notifications_enabled: bool | None = None
    achievement_digest_enabled: bool | None = None
    achievement_digest_frequency: AchievementDigestFrequency | None = None
    achievement_digest_prompt_dismissed: bool | None = None
    achievement_digest_onboarding_seen: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_null_preferences(self) -> Self:
        optional_fields = (
            "email_notifications_enabled",
            "achievement_digest_enabled",
            "achievement_digest_frequency",
            "achievement_digest_prompt_dismissed",
            "achievement_digest_onboarding_seen",
        )
        explicit_nulls = self.model_fields_set.intersection(optional_fields)
        if any(getattr(self, field) is None for field in explicit_nulls):
            raise ValueError("Optional preferences cannot be null.")
        return self
