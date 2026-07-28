from typing import Self

from pydantic import model_validator

from app.modules.identity.schemas.users import AchievementDigestFrequency
from app.shared.schemas import ApiSchema


class UnreadCountResponse(ApiSchema):
    count: int


class NotificationPreferences(ApiSchema):
    email_notifications_enabled: bool
    achievement_digest_enabled: bool
    achievement_digest_frequency: AchievementDigestFrequency
    achievement_digest_prompt_dismissed: bool
    achievement_digest_onboarding_seen: bool


class NotificationPreferencesUpdate(ApiSchema):
    email_notifications_enabled: bool | None = None
    achievement_digest_enabled: bool | None = None
    achievement_digest_frequency: AchievementDigestFrequency | None = None
    achievement_digest_prompt_dismissed: bool | None = None
    achievement_digest_onboarding_seen: bool | None = None

    @model_validator(mode="after")
    def require_non_null_update(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one preference is required.")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Notification preferences cannot be null.")
        return self


__all__ = [
    "NotificationPreferences",
    "NotificationPreferencesUpdate",
    "UnreadCountResponse",
]
