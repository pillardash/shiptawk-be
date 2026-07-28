import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.modules.products.policies.website_url_policy import normalize_website_url

_WHITESPACE = re.compile(r"\s+")
_IGNORED_TAGS = frozenset({"head", "noscript", "script", "style", "svg", "template"})


def _clean(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


@dataclass(frozen=True, slots=True)
class WebsitePageExtraction:
    url: str
    title: str
    meta_description: str | None
    h1: str | None
    headings: tuple[str, ...]
    canonical_url: str | None
    is_indexable: bool
    internal_links: tuple[str, ...]
    text: str
    fingerprint: str

    @property
    def content_fingerprint(self) -> str:
        return self.fingerprint


class _WebsiteHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.heading_parts: list[tuple[str, list[str]]] = []
        self.heading_entries: list[tuple[str, str]] = []
        self.headings: list[str] = []
        self.visible_parts: list[str] = []
        self.meta_description: str | None = None
        self.canonical_href: str | None = None
        self.robots: list[str] = []
        self.links: list[str] = []
        self._tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._tags.append(tag)
        attributes = {name.lower(): value or "" for name, value in attrs}
        if tag in {"h1", "h2", "h3"}:
            self.heading_parts.append((tag, []))
        elif tag == "meta":
            name = attributes.get("name", "").lower()
            if name == "description" and self.meta_description is None:
                self.meta_description = _clean(attributes.get("content", "")) or None
            if name in {"robots", "googlebot"}:
                self.robots.append(attributes.get("content", "").lower())
        elif tag == "link" and "canonical" in attributes.get("rel", "").lower().split():
            self.canonical_href = self.canonical_href or attributes.get("href") or None
        elif tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3"} and self.heading_parts:
            heading_tag, parts = self.heading_parts.pop()
            heading = _clean(" ".join(parts))
            if heading:
                self.headings.append(heading)
                self.heading_entries.append((heading_tag, heading))
        if tag in self._tags:
            reverse_index = self._tags[::-1].index(tag)
            del self._tags[len(self._tags) - reverse_index - 1 :]

    def handle_data(self, data: str) -> None:
        if "title" in self._tags:
            self.title_parts.append(data)
        if self.heading_parts:
            self.heading_parts[-1][1].append(data)
        if not _IGNORED_TAGS.intersection(self._tags):
            self.visible_parts.append(data)


def _normalized_url(reference: str | None, page_url: str) -> str | None:
    if not reference:
        return None
    try:
        return normalize_website_url(urljoin(page_url, reference))
    except ValueError:
        return None


def extract_website_page(html: str, page_url: str) -> WebsitePageExtraction:
    """Extract deterministic, non-rendered website content from HTML."""
    normalized_page_url = normalize_website_url(page_url)
    parser = _WebsiteHTMLParser()
    parser.feed(html)
    parser.close()

    canonical_url = _normalized_url(parser.canonical_href, normalized_page_url)
    page_host = urlsplit(normalized_page_url).hostname
    internal_links = tuple(
        sorted(
            {
                normalized
                for href in parser.links
                if (normalized := _normalized_url(href, normalized_page_url)) is not None
                and urlsplit(normalized).hostname == page_host
            }
        )
    )
    title = _clean(" ".join(parser.title_parts))
    headings = tuple(parser.headings)
    h1 = next((heading for tag, heading in parser.heading_entries if tag == "h1"), None)
    text = _clean(" ".join(parser.visible_parts))
    is_indexable = not any(
        directive.strip() in {"noindex", "none"}
        for robots in parser.robots
        for directive in re.split(r"[\s,]+", robots)
    )
    fingerprint_payload = {
        "canonical_url": canonical_url,
        "headings": headings,
        "is_indexable": is_indexable,
        "meta_description": parser.meta_description,
        "text": text,
        "title": title,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return WebsitePageExtraction(
        url=normalized_page_url,
        title=title,
        meta_description=parser.meta_description,
        h1=h1,
        headings=headings,
        canonical_url=canonical_url,
        is_indexable=is_indexable,
        internal_links=internal_links,
        text=text,
        fingerprint=fingerprint,
    )
