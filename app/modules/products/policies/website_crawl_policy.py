from collections.abc import Iterable
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

from app.modules.products.policies.website_url_policy import normalize_website_url

MAX_CRAWL_PAGES = 50
_EXCLUDED_SEGMENTS = frozenset(
    {
        "admin",
        "api",
        "auth",
        "cart",
        "checkout",
        "login",
        "logout",
        "private",
        "register",
        "signin",
        "signup",
        "wp-admin",
    }
)
_EXCLUDED_EXTENSIONS = frozenset(
    {
        ".avi",
        ".css",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".mov",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".svg",
        ".webp",
        ".xml",
        ".zip",
    }
)
_PRIORITY_SEGMENTS = {"features": 1, "product": 1, "pricing": 2, "docs": 3, "blog": 4}


def _is_crawlable_path(path: str) -> bool:
    segments = tuple(segment.lower() for segment in path.split("/") if segment)
    extension = PurePosixPath(path).suffix.lower()
    return not _EXCLUDED_SEGMENTS.intersection(segments) and extension not in _EXCLUDED_EXTENSIONS


def _priority(url: str) -> int:
    segments = [segment.lower() for segment in urlsplit(url).path.split("/") if segment]
    priorities = (
        _PRIORITY_SEGMENTS[segment] for segment in segments if segment in _PRIORITY_SEGMENTS
    )
    return min(priorities, default=10)


def select_crawl_urls(
    website_url: str,
    candidates: Iterable[str],
    *,
    max_pages: int = MAX_CRAWL_PAGES,
) -> list[str]:
    """Select a deterministic, same-host, bounded set of crawl targets."""
    base_url = normalize_website_url(website_url)
    base_host = urlsplit(base_url).hostname
    discovered: dict[str, int] = {base_url: -1}
    for position, candidate in enumerate(candidates):
        try:
            normalized = normalize_website_url(urljoin(base_url, candidate))
        except ValueError:
            continue
        parsed = urlsplit(normalized)
        if parsed.hostname != base_host or not _is_crawlable_path(parsed.path):
            continue
        discovered.setdefault(normalized, position)

    limit = max(0, min(max_pages, MAX_CRAWL_PAGES))
    ordered = sorted(
        discovered,
        key=lambda url: (0 if url == base_url else 1, _priority(url), discovered[url], url),
    )
    return ordered[:limit]
