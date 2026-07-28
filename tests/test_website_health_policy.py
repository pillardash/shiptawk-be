from app.modules.products.policies.website_health_policy import (
    CrawlPageObservation,
    summarize_website_health,
)


def test_health_summary_keeps_blocked_and_failed_pages_visible() -> None:
    summary = summarize_website_health(
        [
            CrawlPageObservation("https://example.com", status_code=200),
            CrawlPageObservation("https://example.com/private", status_code=403),
            CrawlPageObservation("https://example.com/broken", error="timeout"),
        ]
    )

    assert summary.status == "degraded"
    assert summary.healthy_count == 1
    assert summary.blocked_urls == ("https://example.com/private",)
    assert summary.failures == (("https://example.com/broken", "timeout"),)


def test_health_summary_is_deterministic_and_reports_total_failure() -> None:
    observations = [
        CrawlPageObservation("https://example.com/b", error="connection failed"),
        CrawlPageObservation("https://example.com/a", status_code=503),
    ]

    first = summarize_website_health(observations)
    second = summarize_website_health(reversed(observations))

    assert first == second
    assert first.status == "failed"
    assert first.failures == (
        ("https://example.com/a", "HTTP 503"),
        ("https://example.com/b", "connection failed"),
    )
