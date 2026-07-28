from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.drafts.enums.generation_enum import PrivateRepoMode, Tone


class ContextModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class VoiceProfileInput(ContextModel):
    tone_modes: list[str] = Field(default_factory=list)
    phrases_to_avoid: list[str] = Field(default_factory=list)
    audience_preference: str | None = None
    style_samples: list[str] = Field(default_factory=list)


class SafetySettingsInput(ContextModel):
    blocked_terms: list[str] = Field(default_factory=list)
    blocked_topics: list[str] = Field(default_factory=list)
    ignored_paths: list[str] = Field(default_factory=list)
    preferred_event_types: list[str] = Field(default_factory=list)
    max_drafts_per_week: int
    private_repo_mode: PrivateRepoMode


class UserContextInput(ContextModel):
    tone_preference: Tone
    voice_profile: VoiceProfileInput
    safety_settings: SafetySettingsInput


class ProductContextInput(ContextModel):
    name: str
    description: str | None = None
    target_audience: str | None = None
    messaging_angle: str | None = None
    tone_override: Tone | None = None
    blocked_terms: list[str] = Field(default_factory=list)
    blocked_topics: list[str] = Field(default_factory=list)
    safe_public_boundaries: list[str] = Field(default_factory=list)
    primary_customer_pain: str | None = None
    desired_outcome: str | None = None
    positioning_statement: str | None = None
    proof_points: list[str] = Field(default_factory=list)
    customer_use_cases: list[str] = Field(default_factory=list)
    content_goal: str = ""
    cta_preference: str | None = None
    founder_story_angle: str | None = None


class EffectiveGenerationContext(ContextModel):
    version: Literal[1] = 1
    subject_name: str
    product_name: str | None
    product_description: str
    repo_purpose: str
    repo_role_description: str
    product_audience: str
    repo_audience: str
    account_audience: str
    repo_role: str
    is_primary_repo: bool | None
    messaging_angle: str
    audience_preference: str
    effective_tone_preference: Tone
    tone_modes: list[str]
    phrases_to_avoid: list[str]
    style_anchors: list[str]
    blocked_terms: list[str]
    blocked_topics: list[str]
    safe_public_boundaries: list[str]
    primary_customer_pain: str
    desired_outcome: str
    positioning_statement: str
    proof_points: list[str]
    customer_use_cases: list[str]
    content_goal: str
    cta_preference: str
    founder_story_angle: str
    ignored_paths: list[str]
    preferred_event_types: list[str]
    max_drafts_per_week: int
    private_repo_mode: PrivateRepoMode
