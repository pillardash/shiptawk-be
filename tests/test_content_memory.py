from app.modules.drafts.detectors.repetition_detector import (
    build_content_memory,
    detect_repetition,
    normalize_content,
    similarity_score,
    token_overlap,
)
from app.modules.drafts.schemas.memory import ContentMemory, DraftMemoryInput


def test_normalizes_and_scores_with_legacy_overlap_coefficient() -> None:
    assert normalize_content("We shipped this: https://example.com  Today!") == (
        "we shipped this today"
    )
    assert similarity_score("The", "the") == 1
    assert similarity_score("https://a.test", "https://a.test") == 0
    assert token_overlap("alpha beta gamma", "alpha beta gamma delta epsilon") == 1


def test_builds_ordered_camel_case_snapshot_and_ignores_unknown_angles() -> None:
    memory = build_content_memory(
        [
            DraftMemoryInput(content="First", source_event_payload={"selected_angle": "bad"}),
            DraftMemoryInput(
                content="Second", source_event_payload={"selected_angle": "user_benefit"}
            ),
        ]
    )

    assert memory.model_dump(by_alias=True) == {
        "recentApprovedContent": ["First", "Second"],
        "approvedAngles": ["user_benefit"],
    }


def test_detects_similarity_then_angle_reason_and_caps_penalty() -> None:
    result = detect_repetition(
        candidate="Improved onboarding for teams",
        angle="shipping_update",
        memory=ContentMemory(
            recent_approved_content=["Improved onboarding for teams"],
            approved_angles=["shipping_update", "shipping_update", "shipping_update"],
        ),
    )

    assert result.max_similarity == 1
    assert result.repeated_angle_count == 3
    assert result.penalty == 48
    assert result.reasons == [
        "near_duplicate_recent_draft",
        "repeated_angle:shipping_update",
    ]


def test_empty_memory_has_no_penalty() -> None:
    result = detect_repetition(candidate="New update", angle="user_benefit")

    assert result.model_dump() == {
        "max_similarity": 0.0,
        "repeated_angle_count": 0,
        "penalty": 0,
        "reasons": [],
    }
