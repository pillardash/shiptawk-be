import re
from dataclasses import dataclass
from decimal import Decimal

from app.modules.operator.schemas.opportunity_detection_schema import (
    ProductProfileSnapshot,
    ProfileCapabilitySnapshot,
)

LEXICAL_CAPABILITY_MATCH_POLICY_VERSION = "v1"
QUERY_RELEVANCE_POLICY_VERSION = "v1"
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"})
_NAVIGATION = frozenset({"login", "log in", "signin", "sign in", "pricing", "contact", "support"})


def normalized_tokens(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(
        token[:-1] if token.endswith("s") and len(token) > 3 else token
        for token in _TOKEN.findall(value.casefold())
        if token not in _STOP
    )


@dataclass(frozen=True)
class CapabilityMatch:
    capability_key: str | None
    coverage: Decimal
    relevance: Decimal


def match_capability(
    text: str, capabilities: tuple[ProfileCapabilitySnapshot, ...]
) -> CapabilityMatch:
    text_tokens = normalized_tokens(text)
    matches: list[tuple[Decimal, Decimal, str]] = []
    for capability in capabilities:
        if not capability.active:
            continue
        capability_tokens = normalized_tokens(f"{capability.name} {capability.description or ''}")
        overlap = text_tokens & capability_tokens
        coverage = (
            Decimal(len(overlap)) / len(capability_tokens) if capability_tokens else Decimal(0)
        )
        relevance = Decimal(len(overlap)) / len(text_tokens) if text_tokens else Decimal(0)
        if coverage >= Decimal(".60") and relevance >= Decimal(".20"):
            matches.append((coverage, relevance, capability.capability_key))
    if not matches:
        return CapabilityMatch(None, Decimal(0), Decimal(0))
    matches.sort(reverse=True)
    best = matches[0]
    if len(matches) > 1 and matches[1][:2] == best[:2]:
        return CapabilityMatch(None, Decimal(0), Decimal(0))
    return CapabilityMatch(best[2], best[0], best[1])


@dataclass(frozen=True)
class QueryProfileMatch:
    relevance: Decimal
    branded: bool
    navigational: bool


def query_profile_match(query: str, profile: ProductProfileSnapshot) -> QueryProfileMatch:
    query_tokens = normalized_tokens(query)
    product_tokens = normalized_tokens(profile.product_name)
    capability_tokens = frozenset().union(
        *(
            normalized_tokens(f"{item.name} {item.description or ''}")
            for item in profile.capabilities
        )
    )
    context_tokens = normalized_tokens(
        " ".join(filter(None, (profile.audience, profile.objective, profile.positioning)))
    )
    relevant_tokens = capability_tokens | context_tokens | product_tokens
    overlap = query_tokens & relevant_tokens
    relevance = Decimal(len(overlap)) / len(query_tokens) if query_tokens else Decimal(0)
    branded = bool(product_tokens and product_tokens <= query_tokens)
    normalized_query = " ".join(_TOKEN.findall(query.casefold()))
    navigational = branded and any(term in normalized_query for term in _NAVIGATION)
    return QueryProfileMatch(relevance, branded, navigational)
