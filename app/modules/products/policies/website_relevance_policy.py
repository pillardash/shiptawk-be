import re
from collections.abc import Iterable
from dataclasses import dataclass

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "before",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class RelevancePage:
    url: str
    title: str = ""
    headings: tuple[str, ...] = ()
    text: str = ""


@dataclass(frozen=True, slots=True)
class PageRelevance:
    page: RelevancePage
    score: float
    matched_terms: tuple[str, ...]


def _tokens(value: str) -> set[str]:
    return {token for token in _TOKEN.findall(value.lower()) if token not in _STOP_WORDS}


def score_page_relevance(topic: str, page: RelevancePage) -> PageRelevance:
    """Score lexical evidence with stronger weight for titles and headings."""
    topic_terms = _tokens(topic)
    if not topic_terms:
        return PageRelevance(page=page, score=0.0, matched_terms=())
    title_terms = _tokens(page.title)
    heading_terms = _tokens(" ".join(page.headings))
    text_terms = _tokens(page.text)
    url_terms = _tokens(page.url)
    matched = topic_terms.intersection(title_terms | heading_terms | text_terms | url_terms)
    weighted = sum(
        4.0
        if term in title_terms
        else 2.5
        if term in heading_terms
        else 1.0
        if term in text_terms
        else 0.5
        if term in url_terms
        else 0.0
        for term in topic_terms
    )
    score = round(min(1.0, weighted / (4.0 * len(topic_terms))), 4)
    return PageRelevance(page=page, score=score, matched_terms=tuple(sorted(matched)))


def most_relevant_page(topic: str, pages: Iterable[RelevancePage]) -> PageRelevance | None:
    ranked = sorted(
        (score_page_relevance(topic, page) for page in pages),
        key=lambda result: (-result.score, result.page.url),
    )
    return ranked[0] if ranked and ranked[0].score > 0 else None


def capability_is_represented(
    capability: str,
    pages: Iterable[RelevancePage],
    *,
    minimum_score: float = 0.2,
    minimum_term_coverage: float = 0.6,
) -> bool:
    result = most_relevant_page(capability, pages)
    capability_terms = _tokens(capability)
    if result is None or not capability_terms:
        return False
    coverage = len(result.matched_terms) / len(capability_terms)
    return result.score >= minimum_score and coverage >= minimum_term_coverage
