from app.modules.drafts.policies.scoring import CandidateScoringEngine
from app.modules.drafts.schemas.context import (
    EffectiveGenerationContext,
    ProductContextInput,
    SafetySettingsInput,
    UserContextInput,
    VoiceProfileInput,
)
from app.modules.drafts.schemas.summary import CommitEnrichment
from app.modules.drafts.services.generation_context_service import GenerationContextBuilder
from app.modules.integrations.events import NormalizedEvent
from app.modules.repos.schemas.generation_context_schema import RepositoryContextInput


def context(
    *,
    blocked_terms: list[str] | None = None,
    blocked_topics: list[str] | None = None,
    preferred_events: list[str] | None = None,
    max_drafts: int = 10,
) -> EffectiveGenerationContext:
    return GenerationContextBuilder().build(
        user=UserContextInput(
            tone_preference="casual",
            voice_profile=VoiceProfileInput(
                tone_modes=["concise"],
                phrases_to_avoid=[],
                audience_preference="Product teams",
            ),
            safety_settings=SafetySettingsInput(
                blocked_terms=blocked_terms or [],
                blocked_topics=blocked_topics or [],
                ignored_paths=[],
                preferred_event_types=preferred_events
                if preferred_events is not None
                else ["push", "release", "pull_request_merged"],
                max_drafts_per_week=max_drafts,
                private_repo_mode="normal",
            ),
        ),
        repo=RepositoryContextInput(repo_name="web", repo_full_name="acme/web"),
        product=ProductContextInput(name="Shiptawk", target_audience="Product teams"),
        style_anchors=[],
    )


def event(**updates: object) -> NormalizedEvent:
    values: dict[str, object] = {
        "event_type": "release",
        "repo_full_name": "acme/web",
        "title": "Release",
        "description": "",
        "url": "https://github.com/acme/web/releases/1",
        "occurred_at": "2026-01-01T00:00:00.000Z",
        "sha": None,
        "branch_name": None,
        "touched_paths": [],
        "dedupe_key": "release|acme/web||date|url",
    }
    values.update(updates)
    return NormalizedEvent.model_validate(values)


def test_scores_simple_release_with_exact_legacy_dimensions() -> None:
    result = CandidateScoringEngine().score(
        event=event(),
        enrichment=None,
        repo_private=False,
        generation_context=context(),
        drafts_created_this_week=0,
    )

    assert result.dimensions.model_dump() == {
        "user_impact": 51,
        "novelty": 86,
        "clarity": 42,
        "safety": 100,
        "proof": 56,
    }
    assert result.score == 66
    assert result.outcome == "generate"
    assert result.is_public_worthy is True
    assert result.failed_safety_checks == []


def test_preserves_failure_order_and_hard_block_precedence() -> None:
    result = CandidateScoringEngine().score(
        event=event(title="Token incident roadmap"),
        enrichment=None,
        repo_private=False,
        generation_context=context(
            blocked_terms=["token"],
            blocked_topics=["roadmap"],
            preferred_events=["push"],
            max_drafts=0,
        ),
        drafts_created_this_week=0,
    )

    assert result.failed_safety_checks == [
        "safety:token",
        "safety:roadmap",
        "safety:incident",
        "blocked_term:token",
        "blocked_topic:roadmap",
        "non_preferred_event_type:release",
        "weekly_limit_reached",
    ]
    assert result.dimensions.safety == 0
    assert result.outcome == "block"


def test_low_signal_push_holds_after_safety_dimension_is_computed() -> None:
    result = CandidateScoringEngine().score(
        event=event(
            event_type="push",
            title="Refactor product tests",
            description="Cleanup test types",
            sha="abc",
        ),
        enrichment=None,
        repo_private=False,
        generation_context=context(),
        drafts_created_this_week=0,
    )

    assert result.failed_safety_checks == ["low_product_story_signal"]
    assert result.dimensions.safety == 100
    assert result.outcome == "hold"


def test_verified_enrichment_removes_low_product_story_hold() -> None:
    result = CandidateScoringEngine().score(
        event=event(
            event_type="push",
            title="Refactor product tests",
            description="Cleanup test types",
            sha="abc",
        ),
        enrichment=CommitEnrichment(
            ai_summary="Users get clearer product updates",
            ai_user_benefit="Teams spend less time preparing release content",
        ),
        repo_private=False,
        generation_context=context(),
        drafts_created_this_week=0,
    )

    assert "low_product_story_signal" not in result.failed_safety_checks
