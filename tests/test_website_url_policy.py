import pytest

from app.modules.products.policies.website_url_policy import (
    normalize_website_url,
    validate_public_website_url,
)


def test_normalizes_public_website_url() -> None:
    assert (
        normalize_website_url(" HTTPS://Example.COM:443/features/../pricing/?b=2&a=1#plans ")
        == "https://example.com/pricing/?a=1&b=2"
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "https://user:secret@example.com",
        "http://localhost",
        "http://127.0.0.1",
        "http://[::1]",
        "https://example.local",
        "https://example.com:8080",
    ],
)
def test_rejects_unsafe_url_syntax_host_and_port(url: str) -> None:
    with pytest.raises(ValueError, match="public HTTP"):
        validate_public_website_url(url)


def test_accepts_public_hostname_without_performing_dns() -> None:
    assert validate_public_website_url("https://www.example.com/features") == (
        "https://www.example.com/features"
    )
