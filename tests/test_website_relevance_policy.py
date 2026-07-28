from app.modules.products.policies.website_relevance_policy import (
    RelevancePage,
    capability_is_represented,
    most_relevant_page,
)

PAGES = [
    RelevancePage(
        url="https://example.com/analytics",
        title="Search analytics",
        headings=("Query performance",),
        text="Track ranking and search demand.",
    ),
    RelevancePage(
        url="https://example.com/approvals",
        title="Content approval workflows",
        headings=("Review before publishing",),
        text="Approve generated campaigns before anything goes live.",
    ),
]


def test_reports_capability_as_represented_or_absent() -> None:
    assert capability_is_represented("content approval workflows", PAGES)
    assert capability_is_represented("ranking search demand", PAGES)
    assert not capability_is_represented("customer support ticket routing", PAGES)


def test_selects_most_relevant_page_for_topic() -> None:
    result = most_relevant_page("approve content before publishing", PAGES)

    assert result is not None
    assert result.page.url == "https://example.com/approvals"
    assert result.score > 0


def test_returns_no_page_when_topic_has_no_lexical_evidence() -> None:
    assert most_relevant_page("warehouse inventory forecasting", PAGES) is None
