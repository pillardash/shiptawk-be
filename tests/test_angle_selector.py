from app.domains.generation.angles import AngleSelector
from app.domains.generation.evidence import PublicSafeSummary
from app.domains.integrations.event_normalizer import NormalizedEvent


def event(event_type: str) -> NormalizedEvent:
    return NormalizedEvent.model_validate(
        {
            "event_type": event_type,
            "repo_full_name": "acme/web",
            "title": "Update",
            "description": "",
            "url": "https://github.com/acme/web",
            "occurred_at": "2026-01-01T00:00:00.000Z",
            "sha": None,
            "branch_name": None,
            "touched_paths": [],
            "dedupe_key": "key",
        }
    )


def summary(public: str = "", benefit: str = "") -> PublicSafeSummary:
    return PublicSafeSummary(
        internal_summary="",
        public_summary=public,
        user_benefit=benefit,
        target_audience="",
        safety_flags=[],
        confidence=80,
        supported_claims=[],
    )


def test_push_baseline_preserves_stable_tie_order() -> None:
    selected = AngleSelector().select(event("push"), summary())

    assert selected.best == "user_benefit"
    assert selected.alternates == [
        "technical_credibility",
        "shipping_update",
        "problem_solved",
    ]


def test_release_keyword_tie_keeps_user_benefit_before_milestone() -> None:
    selected = AngleSelector().select(event("release"), summary(public="Release available"))

    assert selected.best == "shipping_update"
    assert selected.alternates == [
        "user_benefit",
        "milestone_or_traction",
        "technical_credibility",
    ]


def test_utf16_benefit_length_triggers_user_benefit_bonus() -> None:
    selected = AngleSelector().select(event("pull_request_merged"), summary(benefit="🚀" * 11))

    assert selected.best == "user_benefit"
    assert "weekly_roundup" not in [selected.best, *selected.alternates]


def test_keyword_rules_use_substrings_and_ascii_version_digits() -> None:
    selected = AngleSelector().select(
        event("push"), summary(public="Debug shipping progress for V2")
    )

    assert selected.best == "problem_solved"
    assert selected.alternates[:2] == ["founder_build_in_public", "user_benefit"]
