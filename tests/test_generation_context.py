from app.modules.drafts.schemas.context import (
    ProductContextInput,
    SafetySettingsInput,
    UserContextInput,
    VoiceProfileInput,
)
from app.modules.drafts.services.generation_context_service import GenerationContextBuilder
from app.modules.repos.schemas.generation_context_schema import RepositoryContextInput


def user() -> UserContextInput:
    return UserContextInput(
        tone_preference="casual",
        voice_profile=VoiceProfileInput(
            tone_modes=[" concise ", "", "concise", "Direct"],
            phrases_to_avoid=[" stealth ", "stealth"],
            audience_preference=" Founders ",
            style_samples=[" account one ", "shared"],
        ),
        safety_settings=SafetySettingsInput(
            blocked_terms=[" secret ", "shared"],
            blocked_topics=["roadmap"],
            ignored_paths=[" docs/ ", "docs/"],
            preferred_event_types=["release", "push", "release"],
            max_drafts_per_week=0,
            private_repo_mode="conservative",
        ),
    )


def product() -> ProductContextInput:
    return ProductContextInput(
        name=" Shiptawk ",
        description=" Turns shipped work into posts ",
        target_audience=" Product teams ",
        messaging_angle=" Benefit first ",
        tone_override="technical",
        blocked_terms=["shared", "Secret", "product"],
        blocked_topics=[" roadmap ", "launch"],
        safe_public_boundaries=[" releases ", "releases"],
        primary_customer_pain=" Inconsistent marketing ",
        desired_outcome=" Weekly momentum ",
        positioning_statement=" An AI marketing operator ",
        proof_points=[" Evidence linked ", "Evidence linked"],
        customer_use_cases=[" launch ", "launch"],
        content_goal="awareness",
        cta_preference=" Try it ",
        founder_story_angle=" Build in public ",
    )


def test_builds_context_with_legacy_precedence_and_stable_lists() -> None:
    context = GenerationContextBuilder().build(
        user=user(),
        repo=RepositoryContextInput(
            repo_name="web", repo_full_name="acme/web", description="Generic repo"
        ),
        product=product(),
        repo_role="frontend",
        is_primary_repo=False,
        repo_role_description=" Dashboard workflow ",
        style_anchors=["shared", " runtime one ", "Runtime One"],
    )

    assert context.subject_name == "Shiptawk"
    assert context.audience_preference == "Product teams"
    assert context.effective_tone_preference == "technical"
    assert context.repo_purpose == context.repo_role_description == "Dashboard workflow"
    assert context.is_primary_repo is False
    assert context.tone_modes == ["concise", "Direct"]
    assert context.style_anchors == ["account one", "shared", "runtime one", "Runtime One"]
    assert context.blocked_terms == ["secret", "shared", "Secret", "product"]
    assert context.blocked_topics == ["roadmap", "launch"]
    assert context.preferred_event_types == ["release", "push", "release"]
    assert context.max_drafts_per_week == 0


def test_builds_no_product_fallback_context() -> None:
    account = user().model_copy(
        update={
            "voice_profile": user().voice_profile.model_copy(
                update={"audience_preference": "   ", "tone_modes": [" "]}
            )
        }
    )

    context = GenerationContextBuilder().build(
        user=account,
        repo=RepositoryContextInput(
            repo_name="api", repo_full_name="acme/api", description=" Backend API "
        ),
        product=None,
        style_anchors=[],
    )

    assert context.subject_name == "api"
    assert context.product_name is None
    assert context.repo_role == "unknown"
    assert context.is_primary_repo is None
    assert context.repo_purpose == "Backend API"
    assert context.audience_preference == "product users and technical founders"
    assert context.tone_modes == ["concise", "direct"]


def test_style_anchors_are_deduplicated_before_ten_item_cap() -> None:
    account = user().model_copy(
        update={
            "voice_profile": user().voice_profile.model_copy(
                update={"style_samples": [f"account-{index}" for index in range(6)]}
            )
        }
    )

    context = GenerationContextBuilder().build(
        user=account,
        repo=RepositoryContextInput(repo_name="web", repo_full_name="acme/web"),
        style_anchors=["account-5", *[f"runtime-{index}" for index in range(6)]],
    )

    assert context.style_anchors == [
        "account-0",
        "account-1",
        "account-2",
        "account-3",
        "account-4",
        "account-5",
        "runtime-0",
        "runtime-1",
        "runtime-2",
        "runtime-3",
    ]


def test_concise_compiler_collapses_whitespace_and_caps_utf16_units() -> None:
    context = GenerationContextBuilder().build(
        user=user(),
        repo=RepositoryContextInput(repo_name="web", repo_full_name="acme/web"),
        product=product().model_copy(update={"description": "word\n\t" * 100}),
        repo_role="frontend",
        is_primary_repo=True,
        repo_role_description="Dashboard   workflow",
        style_anchors=[],
    )

    compiled = GenerationContextBuilder().compile_concise(context)

    assert "Repository role in product: Dashboard workflow" in compiled
    assert len(compiled.encode("utf-16-le", errors="surrogatepass")) // 2 <= 500
