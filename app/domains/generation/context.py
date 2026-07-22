import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Tone = Literal["casual", "technical", "hype"]
PrivateRepoMode = Literal["normal", "conservative"]


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


class RepositoryContextInput(ContextModel):
    repo_name: str
    repo_full_name: str
    description: str | None = None


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


def _trim(value: str | None) -> str:
    return value.strip() if value is not None else ""


def _unique(values: list[str]) -> list[str]:
    normalized = (_trim(value) for value in values)
    return list(dict.fromkeys(value for value in normalized if value))


def _utf16_slice(value: str, limit: int) -> str:
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    return encoded[: limit * 2].decode("utf-16-le", errors="surrogatepass")


class GenerationContextBuilder:
    def build(
        self,
        *,
        user: UserContextInput,
        repo: RepositoryContextInput,
        style_anchors: list[str],
        product: ProductContextInput | None = None,
        repo_role: str | None = None,
        is_primary_repo: bool | None = None,
        repo_role_description: str | None = None,
    ) -> EffectiveGenerationContext:
        product_name = _trim(product.name) if product is not None else ""
        product_audience = _trim(product.target_audience) if product is not None else ""
        account_audience = _trim(user.voice_profile.audience_preference)
        role_description = _trim(repo_role_description) or _trim(repo.description)
        tone_modes = _unique(user.voice_profile.tone_modes)
        product_blocked_terms = _unique(product.blocked_terms if product is not None else [])
        product_blocked_topics = _unique(product.blocked_topics if product is not None else [])

        return EffectiveGenerationContext(
            subject_name=product_name or repo.repo_name or repo.repo_full_name,
            product_name=product_name or None,
            product_description=_trim(product.description) if product is not None else "",
            repo_purpose=role_description,
            repo_role_description=role_description,
            product_audience=product_audience,
            repo_audience="",
            account_audience=account_audience,
            repo_role=repo_role if repo_role is not None else "unknown",
            is_primary_repo=is_primary_repo,
            messaging_angle=_trim(product.messaging_angle) if product is not None else "",
            audience_preference=(
                product_audience or account_audience or "product users and technical founders"
            ),
            effective_tone_preference=(
                product.tone_override
                if product is not None and product.tone_override is not None
                else user.tone_preference
            ),
            tone_modes=tone_modes or ["concise", "direct"],
            phrases_to_avoid=_unique(user.voice_profile.phrases_to_avoid),
            style_anchors=_unique([*user.voice_profile.style_samples, *style_anchors])[:10],
            blocked_terms=_unique([*user.safety_settings.blocked_terms, *product_blocked_terms]),
            blocked_topics=_unique([*user.safety_settings.blocked_topics, *product_blocked_topics]),
            safe_public_boundaries=_unique(
                product.safe_public_boundaries if product is not None else []
            ),
            primary_customer_pain=(
                _trim(product.primary_customer_pain) if product is not None else ""
            ),
            desired_outcome=_trim(product.desired_outcome) if product is not None else "",
            positioning_statement=(
                _trim(product.positioning_statement) if product is not None else ""
            ),
            proof_points=_unique(product.proof_points if product is not None else []),
            customer_use_cases=_unique(product.customer_use_cases if product is not None else []),
            content_goal=product.content_goal if product is not None else "",
            cta_preference=_trim(product.cta_preference) if product is not None else "",
            founder_story_angle=(_trim(product.founder_story_angle) if product is not None else ""),
            ignored_paths=_unique(user.safety_settings.ignored_paths),
            preferred_event_types=user.safety_settings.preferred_event_types,
            max_drafts_per_week=user.safety_settings.max_drafts_per_week,
            private_repo_mode=user.safety_settings.private_repo_mode,
        )

    def compile_concise(self, context: EffectiveGenerationContext) -> str:
        def normalize(value: str, limit: int) -> str:
            collapsed = re.sub(r"\s+", " ", value).strip()
            return _utf16_slice(collapsed, limit)

        lines = ["Context version: 1"]
        if context.product_name:
            lines.append(f"Product: {normalize(context.product_name, 80)}")
        if context.product_description:
            lines.append(f"Product description: {normalize(context.product_description, 140)}")
        if context.product_audience:
            lines.append(f"Product audience: {normalize(context.product_audience, 80)}")
        if context.repo_role_description:
            lines.append(
                f"Repository role in product: {normalize(context.repo_role_description, 160)}"
            )
        if context.account_audience:
            lines.append(f"Account audience: {normalize(context.account_audience, 80)}")
        lines.append(f"Repository role: {context.repo_role}")
        if context.is_primary_repo is not None:
            lines.append(f"Primary repository: {'yes' if context.is_primary_repo else 'no'}")
        return _utf16_slice("\n".join(lines), 500)
