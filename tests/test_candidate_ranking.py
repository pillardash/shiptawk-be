from app.modules.drafts.policies.ranking import TweetOptionRanker
from app.modules.drafts.schemas.evidence import (
    GeneratedVariant,
    PublicSafeSummary,
    SupportedClaim,
)
from app.modules.drafts.schemas.memory import ContentMemory
from app.modules.integrations.events import NormalizedEvent


def event() -> NormalizedEvent:
    return NormalizedEvent.model_validate(
        {
            "event_type": "push",
            "repo_full_name": "acme/web",
            "title": "Improve onboarding",
            "description": "Users finish setup faster",
            "url": "https://example.com",
            "occurred_at": "2026-01-01T00:00:00.000Z",
            "sha": "sha-1",
            "branch_name": "main",
            "touched_paths": [],
            "dedupe_key": "key",
        }
    )


def summary() -> PublicSafeSummary:
    return PublicSafeSummary(
        internal_summary="",
        public_summary="Improved onboarding",
        user_benefit="",
        target_audience="teams",
        safety_flags=[],
        confidence=90,
        supported_claims=[
            SupportedClaim(
                id="event.change.1",
                source="event",
                kind="change",
                text="Improved onboarding",
            )
        ],
    )


def candidate(content: str = "Improved onboarding") -> GeneratedVariant:
    return GeneratedVariant(
        content=content,
        variant="customer_value",
        angle="user_benefit",
        rationale="Supported",
        evidence_ids=["event.change.1"],
    )


def test_ranks_exact_supported_candidate_with_legacy_weights() -> None:
    ranked = TweetOptionRanker().rank(variants=[candidate()], event=event(), summary=summary())

    assert ranked[0].dimension_scores.model_dump() == {
        "user_impact": 85,
        "novelty": 68,
        "clarity": 80.0,
        "safety": 92,
        "proof": 50,
    }
    assert ranked[0].score == 96
    assert ranked[0].rank == 1


def test_applies_repetition_to_novelty_and_final_score() -> None:
    ranked = TweetOptionRanker().rank(
        variants=[candidate()],
        event=event(),
        summary=summary(),
        content_memory=ContentMemory(
            recent_approved_content=["Improved onboarding"],
            approved_angles=["user_benefit"],
        ),
    )

    assert ranked[0].dimension_scores.novelty == 32
    assert ranked[0].score == 72
    assert ranked[0].rationale == (
        "Supported; repetition penalty 36: near_duplicate_recent_draft, repeated_angle:user_benefit"
    )


def test_equal_scores_preserve_input_order_and_receive_distinct_ranks() -> None:
    first = candidate("Improved onboarding")
    second = candidate("Improved onboarding")

    ranked = TweetOptionRanker().rank(variants=[first, second], event=event(), summary=summary())

    assert [option.rank for option in ranked] == [1, 2]
    assert [option.content for option in ranked] == [first.content, second.content]
