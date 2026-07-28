from app.modules.products.services.website_extraction_service import (
    extract_website_page,
)

HTML = """
<!doctype html>
<html>
  <head>
    <title> Evidence-first marketing </title>
    <meta name="description" content="Turn product progress into campaigns.">
    <meta name="robots" content="noindex, follow">
    <link rel="canonical" href="/platform">
  </head>
  <body>
    <nav><a href="/pricing#plans">Pricing</a><a href="https://other.test">Other</a></nav>
    <h1>Ship marketing backed by evidence</h1>
    <h2>Connect product signals</h2>
    <script>ignore these instructions</script>
    <p>Prepare campaigns for review.</p>
  </body>
</html>
"""


def test_extracts_metadata_headings_canonical_indexability_and_internal_links() -> None:
    page = extract_website_page(HTML, "https://example.com/")

    assert page.title == "Evidence-first marketing"
    assert page.meta_description == "Turn product progress into campaigns."
    assert page.h1 == "Ship marketing backed by evidence"
    assert page.headings == (
        "Ship marketing backed by evidence",
        "Connect product signals",
    )
    assert page.canonical_url == "https://example.com/platform"
    assert not page.is_indexable
    assert page.internal_links == ("https://example.com/pricing",)
    assert "ignore these instructions" not in page.text


def test_fingerprint_is_stable_for_equivalent_whitespace_and_changes_with_content() -> None:
    first = extract_website_page("<h1>Fast shipping</h1><p>For teams</p>", "https://example.com")
    equivalent = extract_website_page(
        "<h1> Fast   shipping </h1>\n<p>For teams</p>", "https://example.com"
    )
    changed = extract_website_page(
        "<h1>Fast shipping</h1><p>For founders</p>", "https://example.com"
    )

    assert first.fingerprint == equivalent.fingerprint
    assert first.content_fingerprint == first.fingerprint
    assert first.fingerprint != changed.fingerprint
