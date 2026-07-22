from app.domains.generation.context import (
    EffectiveGenerationContext,
    GenerationContextBuilder,
    ProductContextInput,
    RepositoryContextInput,
    SafetySettingsInput,
    UserContextInput,
    VoiceProfileInput,
)
from app.domains.generation.summary import CommitEnrichment, PublicSafeSummaryEngine
from app.domains.integrations.event_normalizer import NormalizedEvent


def context(**product_updates: object) -> EffectiveGenerationContext:
    product_values: dict[str, object] = {
        "name": "Shiptawk",
        "description": "Turns shipped work into posts",
        "target_audience": "Product teams",
        "proof_points": ["Evidence linked", "token=proof-secret"],
    }
    product_values.update(product_updates)
    return GenerationContextBuilder().build(
        user=UserContextInput(
            tone_preference="casual",
            voice_profile=VoiceProfileInput(
                tone_modes=["concise"],
                phrases_to_avoid=["stealth"],
                audience_preference="Founders",
            ),
            safety_settings=SafetySettingsInput(
                blocked_terms=["incident"],
                blocked_topics=["roadmap"],
                ignored_paths=[],
                preferred_event_types=["release"],
                max_drafts_per_week=3,
                private_repo_mode="normal",
            ),
        ),
        repo=RepositoryContextInput(repo_name="web", repo_full_name="acme/web"),
        product=ProductContextInput.model_validate(product_values),
        style_anchors=[],
    )


def event(**updates: object) -> NormalizedEvent:
    values: dict[str, object] = {
        "event_type": "release",
        "repo_full_name": "acme/web",
        "title": "Fix incident",
        "description": "token=abc123 customer foo internal ticket #123",
        "url": "https://github.com/acme/web/releases/1",
        "occurred_at": "2026-01-01T00:00:00.000Z",
        "sha": None,
        "branch_name": None,
        "touched_paths": [],
        "dedupe_key": "release|acme/web||date|url",
    }
    values.update(updates)
    return NormalizedEvent.model_validate(values)


def test_redacts_in_order_and_preserves_raw_internal_summary() -> None:
    result = PublicSafeSummaryEngine().build(event=event(), generation_context=context())

    assert result.internal_summary == (
        "Fix incident. token=abc123 customer foo internal ticket #123"
    )
    assert result.public_summary == "Fix incident. [redacted] [redacted] [redacted]"
    assert result.safety_flags == ["keyword:incident", "blocked_term:incident"]


def test_phrase_avoidance_and_sensitive_enrichment_drop_claims() -> None:
    result = PublicSafeSummaryEngine().build(
        event=event(title="Stealth launch", description="Public release"),
        generation_context=context(),
        enrichment=CommitEnrichment(
            ai_summary="Configured token=super-secret for customer acme@example.com",
            ai_user_benefit="Customer acme@example.com can use password=hunter2",
        ),
    )

    assert "stealth" not in result.public_summary.lower()
    assert "super-secret" not in result.public_summary
    assert "hunter2" not in result.user_benefit
    assert "[removed]" in result.public_summary
    assert all(claim.source != "enrichment" for claim in result.supported_claims or [])


def test_claim_order_and_product_sanitization_compatibility() -> None:
    result = PublicSafeSummaryEngine().build(
        event=event(title="Release", description="Improved onboarding"),
        generation_context=context(description="token=raw-product-secret"),
    )

    claims = result.supported_claims or []
    assert [claim.id for claim in claims] == [
        "event.change.1",
        "event.change.2",
        "product.identity.1",
        "product.capability.1",
        "product.audience.1",
        "product.proof.1",
    ]
    assert claims[3].text == "token=raw-product-secret"


def test_confidence_uses_javascript_utf16_title_length() -> None:
    result = PublicSafeSummaryEngine().build(
        event=event(title="🚀🚀🚀🚀🚀", url="HTTP://example.test"),
        generation_context=context(),
        enrichment=CommitEnrichment(ai_summary=None, ai_user_benefit=None),
    )

    assert result.confidence == 72
