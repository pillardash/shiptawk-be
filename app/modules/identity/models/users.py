from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel


class User(BaseModel):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "tone_preference IN ('casual', 'technical', 'hype')", name="users_tone_preference"
        ),
        CheckConstraint(
            "achievement_digest_frequency IN ('weekly', 'monthly')",
            name="users_achievement_digest_frequency",
        ),
    )

    email: Mapped[str | None] = mapped_column(String(320), index=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Temporary non-secret frontend compatibility fields. OAuthIdentity is authoritative.
    github_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    github_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    github_installation_id: Mapped[int | None] = mapped_column(nullable=True)
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    tone_preference: Mapped[str] = mapped_column(
        String(20), nullable=False, default="casual", server_default="casual"
    )
    email_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    achievement_digest_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    achievement_digest_frequency: Mapped[str] = mapped_column(
        String(20), nullable=False, default="weekly", server_default="weekly"
    )
    achievement_digest_prompt_dismissed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    achievement_digest_onboarding_seen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_notified_pending_draft_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_dashboard_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voice_profile: Mapped[dict[str, object]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        nullable=False,
        default=dict,
        server_default=text(
            '\'{"tone_modes": ["concise", "direct"], "style_samples": [], '
            '"phrases_to_avoid": [], "audience_preference": ""}\''
        ),
    )
    safety_settings: Mapped[dict[str, object]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        nullable=False,
        default=dict,
        server_default=text(
            '\'{"blocked_terms": [], "ignored_paths": [], "blocked_topics": [], '
            '"private_repo_mode": "normal", "max_drafts_per_week": 14, '
            '"preferred_event_types": ["push", "release", '
            '"pull_request_merged"]}\''
        ),
    )
