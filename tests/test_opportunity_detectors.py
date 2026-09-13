from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.modules.operator.detectors import (
    existing_page_needs_improvement_detector as page_detector,
)
from app.modules.operator.detectors import (
    high_impressions_low_ctr_detector as ctr_detector,
)
from app.modules.operator.detectors import (
    ranking_within_reach_detector as ranking_detector,
)
from app.modules.operator.detectors import (
    search_decline_detector as decline_detector,
)
from app.modules.operator.detectors import (
    search_demand_without_dedicated_content_detector as gap_detector,
)
from app.modules.operator.detectors import (
    shipped_but_not_marketed_detector as shipped_detector,
)
from app.modules.operator.detectors.opportunity_registry_detector import (
    DETECTOR_REGISTRY,
    validate_registry,
)
from app.modules.operator.schemas import DetectionInput, DetectorEvaluation

NOW = datetime(2026, 7, 27, tzinfo=UTC)


def metric(
    clicks: int = 1, impressions: int = 100, ctr: str = ".01", position: str = "8"
) -> dict[str, object]:
    return {"clicks": clicks, "impressions": impressions, "ctr": ctr, "average_position": position}


def query(key: str = "q", **changes: object) -> dict[str, object]:
    value: dict[str, object] = dict(
        query_fingerprint=key,
        query_text=f"query {key}",
        current=metric(),
        prior=metric(),
        matched_page_fingerprint="page",
        page_match_status="matched",
        profile_relevance=".20",
    )
    value.update(changes)
    return value


def data(
    *,
    queries: list[dict[str, object]] | None = None,
    search_pages: list[dict[str, object]] | None = None,
    website_pages: list[dict[str, object]] | None = None,
    mappings: list[dict[str, object]] | None = None,
    events: list[dict[str, object]] | None = None,
    crawl_at: datetime | None = None,
) -> DetectionInput:
    return DetectionInput.model_validate(
        {
            "schema_version": "1",
            "workspace_id": uuid4(),
            "product_id": uuid4(),
            "as_of": NOW,
            "profile": {"objective": "Grow", "profile_version": 3},
            "search": {
                "prior_start": date(2026, 6, 2),
                "prior_end": date(2026, 6, 29),
                "current_start": date(2026, 6, 30),
                "current_end": date(2026, 7, 27),
                "queries": queries or [],
                "pages": search_pages or [],
            },
            "website": {
                "profile_version": 3,
                "completed_at": crawl_at or NOW - timedelta(days=1),
                "pages": website_pages or [],
                "capability_mappings": mappings or [],
            },
            "shipping": {"events": events or []},
        }
    )


def reason(result: tuple[DetectorEvaluation, ...]) -> str:
    return result[0].reason_codes[0].value


class TestHighImpressionsLowCtrDetector:
    def test_exact_floor_shortfall_and_boundaries_pass(self) -> None:
        current = metric(clicks=0, impressions=149, ctr=".019", position="10")
        result = ctr_detector.detect(data(queries=[query(current=current)]))
        assert result[0].outcome.value == "accepted"
        assert result[0].candidate.evidence[0].source_type.value == "search"  # type: ignore[union-attr]

    def test_floor_shortfall_fail_and_low_sample_precedes_ambiguity(self) -> None:
        assert (
            reason(
                ctr_detector.detect(
                    data(queries=[query(current=metric(clicks=1, impressions=149, ctr=".019"))])
                )
            )
            == "threshold_not_met"
        )
        low = query(
            current=metric(impressions=99),
            page_match_status="ambiguous",
            matched_page_fingerprint=None,
        )
        assert reason(ctr_detector.detect(data(queries=[low]))) == "threshold_not_met"


class TestRankingWithinReachDetector:
    def test_passes_exact_boundaries_and_rejects_nonindexable(self) -> None:
        page = {"page_fingerprint": "page", "url": "https://x.test/page", "indexable": True}
        assert (
            ranking_detector.detect(
                data(
                    queries=[query(current=metric(impressions=100, position="20", ctr=".03"))],
                    website_pages=[page],
                )
            )[0].outcome.value
            == "accepted"
        )
        page["indexable"] = False
        assert (
            reason(
                ranking_detector.detect(
                    data(
                        queries=[query(current=metric(position="11", ctr=".03"))],
                        website_pages=[page],
                    )
                )
            )
            == "target_page_nonindexable"
        )

    def test_excludes_only_owned_low_ctr_overlap(self) -> None:
        page: dict[str, object] = {"page_fingerprint": "page", "url": "https://x.test/page"}
        owned = query(current=metric(clicks=0, impressions=100, ctr=".01", position="10"))
        assert (
            reason(ranking_detector.detect(data(queries=[owned], website_pages=[page])))
            == "overlapping_detector_owner"
        )


class TestSearchDeclineDetector:
    def test_zero_prior_is_safe_and_shared_page_emits_only_largest_loss(self) -> None:
        zero = query("zero", current=metric(0, 0), prior=metric(0, 0))
        a = query("a", current=metric(5, 100), prior=metric(20, 300))
        b = query("b", current=metric(5, 100), prior=metric(15, 250))
        result = decline_detector.detect(data(queries=[zero, a, b]))
        accepted = [item for item in result if item.outcome.value == "accepted"]
        assert len(accepted) == 1 and accepted[0].candidate.subject_keys == {"query": "a"}  # type: ignore[union-attr]
        superseded = [item for item in result if item.outcome.value == "superseded_in_run"]
        assert len(superseded) == 1
        assert [reason.value for reason in superseded[0].reason_codes] == ["lower_priority"]


class TestShippedButNotMarketedDetector:
    def event(self) -> dict[str, object]:
        return {
            "event_key": "release",
            "occurred_at": NOW - timedelta(days=2),
            "safe_summary": "Shipped export",
            "capability_key": "export",
        }

    def test_requires_post_event_crawl_and_emits_missing_with_typed_evidence(self) -> None:
        assert (
            reason(
                shipped_detector.detect(
                    data(events=[self.event()], crawl_at=NOW - timedelta(days=3))
                )
            )
            == "website_stale"
        )
        result = shipped_detector.detect(data(events=[self.event()]))[0]
        assert result.candidate.action_family.value == "product_update"  # type: ignore[union-attr]
        assert {e.source_type.value for e in result.candidate.evidence} == {"shipping", "website"}  # type: ignore[union-attr]
        assert "raw_payload" not in str(result.model_dump()) and "private" not in str(
            result.model_dump()
        )

    def test_profile_mapping_threshold_selects_partial_action(self) -> None:
        mapping = {
            "capability_key": "export",
            "page_fingerprint": "page",
            "profile_version": 3,
            "coverage_score": ".60",
            "relevance_score": ".20",
            "status": "partial",
        }
        result = shipped_detector.detect(data(events=[self.event()], mappings=[mapping]))[0]
        assert result.candidate.action_family.value == "existing_page_optimization"  # type: ignore[union-attr]

    def test_complete_mapping_and_ineligible_event_are_rejected(self) -> None:
        mapping = {
            "capability_key": "export",
            "page_fingerprint": "page",
            "profile_version": 3,
            "coverage_score": "1",
            "relevance_score": "1",
            "status": "complete",
        }
        assert (
            reason(shipped_detector.detect(data(events=[self.event()], mappings=[mapping])))
            == "capability_mapping_current"
        )
        event = {**self.event(), "evaluation_outcome": "ignore"}
        assert reason(shipped_detector.detect(data(events=[event]))) == "shipping_signal_ineligible"


class TestContentGapDetector:
    @pytest.mark.parametrize(
        ("changes", "expected"),
        [
            ({"page_match_status": "ambiguous"}, "target_page_ambiguous"),
            ({"branded": True}, "navigation_or_branded_query"),
            ({"navigational": True}, "navigation_or_branded_query"),
            ({"profile_relevance": ".19"}, "threshold_not_met"),
        ],
    )
    def test_rejections(self, changes: dict[str, object], expected: str) -> None:
        assert reason(gap_detector.detect(data(queries=[query(**changes)]))) == expected  # type: ignore[arg-type]

    def test_exact_threshold_passes_and_low_sample_precedes_ambiguity(self) -> None:
        assert (
            gap_detector.detect(
                data(
                    queries=[
                        query(
                            current=metric(impressions=50),
                            page_match_status="unmatched",
                            matched_page_fingerprint=None,
                        )
                    ]
                )
            )[0].outcome.value
            == "accepted"
        )
        q = query(current=metric(impressions=49), page_match_status="ambiguous")
        assert reason(gap_detector.detect(data(queries=[q]))) == "threshold_not_met"


class TestPageImprovementDetector:
    @pytest.mark.parametrize(
        "field", ["title", "meta_description", "h1s", "indexable", "canonical_fingerprint"]
    )
    def test_each_defined_defect_is_reported(self, field: str) -> None:
        page: dict[str, object] = {
            "page_fingerprint": "page",
            "url": "https://x.test/page",
            "title": "Title",
            "meta_description": "Description",
            "h1s": ["Heading"],
            "indexable": True,
            "canonical_fingerprint": "page",
        }
        page[field] = {
            "title": None,
            "meta_description": None,
            "h1s": [],
            "indexable": False,
            "canonical_fingerprint": "other",
        }[field]
        search_page: dict[str, object] = {
            "url_fingerprint": "search-page",
            "url": page["url"],
            "website_page_fingerprint": "page",
            "match_status": "matched",
            "current": metric(impressions=50),
            "prior": metric(),
        }
        candidate = page_detector.detect(data(search_pages=[search_page], website_pages=[page]))[
            0
        ].candidate
        assert candidate.action_family.value == "page_quality_fix"  # type: ignore[union-attr]

    def test_reports_no_defect_missing_match_and_failed_page_crawl(self) -> None:
        page: dict[str, object] = {
            "page_fingerprint": "page",
            "url": "https://x.test/page",
            "title": "Title",
            "meta_description": "Description",
            "h1s": ["Heading"],
        }
        search_page: dict[str, object] = {
            "url_fingerprint": "sp",
            "url": page["url"],
            "website_page_fingerprint": "page",
            "match_status": "matched",
            "current": metric(impressions=50),
            "prior": metric(),
        }
        assert (
            reason(page_detector.detect(data(search_pages=[search_page], website_pages=[page])))
            == "no_page_quality_defect"
        )
        search_page["match_status"] = "unmatched"
        assert (
            reason(page_detector.detect(data(search_pages=[search_page], website_pages=[page])))
            == "target_page_missing"
        )
        search_page["match_status"] = "matched"
        page["crawl_status"] = "failed"
        assert (
            reason(page_detector.detect(data(search_pages=[search_page], website_pages=[page])))
            == "website_crawl_failed"
        )

    def test_conversion_path_requires_an_aligned_observed_destination(self) -> None:
        page: dict[str, object] = {
            "page_fingerprint": "page",
            "url": "https://x.test/features",
            "title": "Title",
            "meta_description": "Description",
            "h1s": ["Heading"],
            "internal_links": ["https://x.test/contact"],
        }
        conversion_page: dict[str, object] = {
            "page_fingerprint": "conversion",
            "url": "https://x.test/signup",
            "page_type": "conversion",
        }
        search_page: dict[str, object] = {
            "url_fingerprint": "sp",
            "url": page["url"],
            "website_page_fingerprint": "page",
            "match_status": "matched",
            "current": metric(impressions=50),
            "prior": metric(),
        }

        result = page_detector.detect(
            data(search_pages=[search_page], website_pages=[page, conversion_page])
        )[0]
        assert result.outcome.value == "accepted"

        page["internal_links"] = ["https://x.test/signup/"]
        assert (
            reason(
                page_detector.detect(
                    data(search_pages=[search_page], website_pages=[page, conversion_page])
                )
            )
            == "no_page_quality_defect"
        )

    def test_profile_conversion_url_takes_precedence_over_inventory_destinations(self) -> None:
        value = data(
            search_pages=[
                {
                    "url_fingerprint": "sp",
                    "url": "https://x.test/features",
                    "website_page_fingerprint": "page",
                    "match_status": "matched",
                    "current": metric(impressions=50),
                    "prior": metric(),
                }
            ],
            website_pages=[
                {
                    "page_fingerprint": "page",
                    "url": "https://x.test/features",
                    "title": "Title",
                    "meta_description": "Description",
                    "h1s": ["Heading"],
                    "internal_links": ["https://x.test/demo"],
                },
                {
                    "page_fingerprint": "demo",
                    "url": "https://x.test/demo",
                    "page_type": "conversion",
                },
            ],
        )
        value = value.model_copy(
            update={
                "profile": value.profile.model_copy(
                    update={"conversion_url": "https://x.test/signup"}
                )
            }
        )
        assert page_detector.detect(value)[0].outcome.value == "accepted"


def test_registry_is_exact_ordered_unique_and_rejects_duplicates() -> None:
    assert [(item[0], item[1]) for item in DETECTOR_REGISTRY] == [
        ("high_impressions_low_ctr", "1"),
        ("ranking_within_reach", "1"),
        ("search_decline", "1"),
        ("shipped_but_not_marketed", "1"),
        ("search_demand_without_dedicated_content", "1"),
        ("existing_page_needs_improvement", "2"),
    ]
    with pytest.raises(ValueError):
        validate_registry((DETECTOR_REGISTRY[0],) * 6)
    with pytest.raises(ValueError):
        validate_registry(DETECTOR_REGISTRY[:5])


def test_each_detector_stops_when_its_required_source_is_missing() -> None:
    base = data()
    without_search = base.model_copy(update={"search": None})
    without_website = base.model_copy(update={"website": None})
    for detector in (ctr_detector, decline_detector, gap_detector):
        assert detector.detect(without_search)[0].outcome.value == "stopped"
    for detector in (ranking_detector, page_detector):
        assert detector.detect(without_website)[0].outcome.value == "stopped"
    assert shipped_detector.detect(without_website)[0].outcome.value == "stopped"
