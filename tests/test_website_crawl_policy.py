from app.modules.products.policies.website_crawl_policy import select_crawl_urls


def test_selects_deduplicated_same_site_urls_and_excludes_unsafe_paths() -> None:
    selected = select_crawl_urls(
        "https://example.com",
        [
            "https://example.com/#top",
            "/features",
            "/features#details",
            "/admin/users",
            "/api/private",
            "https://other.example/pricing",
            "/assets/logo.png",
        ],
    )

    assert selected == ["https://example.com/", "https://example.com/features"]


def test_selection_has_a_hard_fifty_page_cap() -> None:
    selected = select_crawl_urls(
        "https://example.com",
        [f"/blog/post-{index}" for index in range(100)],
        max_pages=500,
    )

    assert len(selected) == 50
    assert len(set(selected)) == 50


def test_priority_pages_are_selected_before_generic_pages() -> None:
    candidates = [f"/misc/{index}" for index in range(50)] + ["/pricing", "/features"]

    selected = select_crawl_urls("https://example.com", candidates)

    assert "https://example.com/pricing" in selected
    assert "https://example.com/features" in selected
