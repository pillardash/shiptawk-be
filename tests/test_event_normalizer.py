import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.modules.integrations.events import EventNormalizer
from app.modules.integrations.policies.branches import should_process_tracked_branch


def load_fixtures() -> list[dict[str, Any]]:
    fixture_path = Path(__file__).parent / "fixtures" / "event_normalization.json"
    return json.loads(fixture_path.read_text())  # type: ignore[no-any-return]


def test_event_normalizer_matches_canonical_typescript_fixtures() -> None:
    normalizer = EventNormalizer()

    for fixture in load_fixtures():
        event = normalizer.normalize(
            fixture["eventName"], fixture["payload"], fixture["repoFullName"]
        )
        actual = event.model_dump(exclude_none=False) if event is not None else None
        if actual is not None and actual["push_context"] is None:
            actual.pop("push_context")
        assert actual == fixture["expected"], fixture["name"]


def test_missing_timestamp_uses_javascript_compatible_injected_clock() -> None:
    normalizer = EventNormalizer(clock=lambda: datetime(2026, 7, 21, 12, 34, 56, 789000, UTC))

    event = normalizer.normalize("release", {"release": {}}, "acme/web")

    assert event is not None
    assert event.occurred_at == "2026-07-21T12:34:56.789Z"
    assert event.dedupe_key.endswith(
        "|2026-07-21T12:34:56.789Z|https://github.com/acme/web/releases"
    )


def test_push_rejects_tag_ref_as_branch_but_still_normalizes() -> None:
    event = EventNormalizer().normalize(
        "push",
        {
            "ref": "refs/tags/v1",
            "head_commit": {
                "id": "sha-1",
                "message": "Release",
                "url": "https://github.com/acme/web/commit/sha-1",
                "timestamp": "2026-01-01T00:00:00.000Z",
            },
        },
        "acme/web",
    )

    assert event is not None
    assert event.branch_name is None


def test_empty_provider_timestamp_is_preserved_for_legacy_parity() -> None:
    event = EventNormalizer().normalize("release", {"release": {"published_at": ""}}, "acme/web")

    assert event is not None
    assert event.occurred_at == ""


def test_tracked_branch_filter_matches_legacy_rules() -> None:
    push = EventNormalizer().normalize(
        "push",
        {
            "ref": "refs/heads/main",
            "head_commit": {"timestamp": "2026-01-01T00:00:00.000Z"},
        },
        "acme/web",
    )
    release = EventNormalizer().normalize(
        "release",
        {"release": {"published_at": "2026-01-01T00:00:00.000Z"}},
        "acme/web",
    )
    assert push is not None and release is not None

    assert should_process_tracked_branch(None, push) is True
    assert should_process_tracked_branch(" main ", push) is True
    assert should_process_tracked_branch("production", push) is False
    assert should_process_tracked_branch("production", release) is True
