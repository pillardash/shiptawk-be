import re

from pydantic import BaseModel, ConfigDict

from app.domains.generation.context import EffectiveGenerationContext
from app.domains.generation.evidence import PublicSafeSummary, SupportedClaim
from app.domains.integrations.event_normalizer import NormalizedEvent

_FLAGS = re.IGNORECASE | re.ASCII
_REDACTION_PATTERNS = [
    re.compile(r"token\s*[:=]\s*[^\s]+", _FLAGS),
    re.compile(r"password\s*[:=]\s*[^\s]+", _FLAGS),
    re.compile(r"api[_\s-]?key\s*[:=]\s*[^\s]+", _FLAGS),
    re.compile(r"bearer\s+[a-z0-9._-]+", _FLAGS),
    re.compile(r"[a-f0-9]{32,}", _FLAGS),
    re.compile(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", _FLAGS),
    re.compile(r"customer\s+[a-z0-9._-]+", _FLAGS),
    re.compile(r"internal\s+ticket\s*#?\d+", _FLAGS),
]
_SAFETY_KEYWORDS = [
    "secret",
    "token",
    "bearer",
    "password",
    "api key",
    "customer",
    "internal",
    "roadmap",
    "exploit",
    "cve",
    "incident",
    "hotfix",
]


class CommitEnrichment(BaseModel):
    model_config = ConfigDict(frozen=True)

    ai_summary: str | None
    ai_user_benefit: str | None


def _sanitize(value: str) -> str:
    output = value
    for pattern in _REDACTION_PATTERNS:
        output = pattern.sub("[redacted]", output)
    return re.sub(r"\s+", " ", output).strip()


def _avoid_phrases(value: str, phrases: list[str]) -> str:
    output = value
    for phrase in phrases:
        safe_phrase = phrase.strip()
        if safe_phrase:
            output = re.sub(
                re.escape(safe_phrase), "[removed]", output, flags=re.IGNORECASE | re.ASCII
            )
    return output


def _add_claim(claims: list[SupportedClaim], claim: SupportedClaim) -> None:
    if not claim.text or "[redacted]" in claim.text or "[removed]" in claim.text:
        return
    if any(existing.id == claim.id for existing in claims):
        return
    claims.append(claim)


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


class PublicSafeSummaryEngine:
    def build(
        self,
        *,
        event: NormalizedEvent,
        generation_context: EffectiveGenerationContext,
        enrichment: CommitEnrichment | None = None,
    ) -> PublicSafeSummary:
        phrases = generation_context.phrases_to_avoid
        internal_summary = f"{event.title}. {event.description}".strip()
        safe_title = _avoid_phrases(_sanitize(event.title), phrases)
        safe_description = _avoid_phrases(_sanitize(event.description), phrases)
        safe_public_base = f"{safe_title}. {safe_description}".strip()
        enrichment_summary = _avoid_phrases(
            _sanitize(
                enrichment.ai_summary.strip() if enrichment and enrichment.ai_summary else ""
            ),
            phrases,
        )
        safe_public = (
            f"{safe_public_base}. {enrichment_summary}" if enrichment_summary else safe_public_base
        )
        lowered = safe_public.lower()
        safety_flags = [
            *(f"keyword:{keyword}" for keyword in _SAFETY_KEYWORDS if keyword in lowered),
            *(
                f"blocked_term:{term}"
                for term in generation_context.blocked_terms
                if term.lower() in lowered
            ),
            *(
                f"blocked_topic:{topic}"
                for topic in generation_context.blocked_topics
                if topic.lower() in lowered
            ),
        ]
        enrichment_benefit = _avoid_phrases(
            _sanitize(
                enrichment.ai_user_benefit.strip()
                if enrichment and enrichment.ai_user_benefit
                else ""
            ),
            phrases,
        )
        target_audience = (
            generation_context.audience_preference.strip()
            or "current users and developers following product updates"
        )

        claims: list[SupportedClaim] = []
        for claim in (
            SupportedClaim(id="event.change.1", source="event", kind="change", text=safe_title),
            SupportedClaim(
                id="event.change.2", source="event", kind="change", text=safe_description
            ),
            SupportedClaim(
                id="enrichment.change.1",
                source="enrichment",
                kind="change",
                text=enrichment_summary,
            ),
            SupportedClaim(
                id="enrichment.benefit.1",
                source="enrichment",
                kind="benefit",
                text=enrichment_benefit,
            ),
            SupportedClaim(
                id="product.identity.1",
                source="product_context",
                kind="product_identity",
                text=generation_context.product_name or "",
            ),
            SupportedClaim(
                id="product.capability.1",
                source="product_context",
                kind="product_capability",
                text=generation_context.product_description,
            ),
            SupportedClaim(
                id="product.audience.1",
                source="product_context",
                kind="audience",
                text=generation_context.product_audience,
            ),
        ):
            _add_claim(claims, claim)

        for index, proof_point in enumerate(generation_context.proof_points, start=1):
            _add_claim(
                claims,
                SupportedClaim(
                    id=f"product.proof.{index}",
                    source="product_context",
                    kind="proof_point",
                    text=_avoid_phrases(_sanitize(proof_point), phrases),
                ),
            )

        confidence = max(
            0,
            min(
                100,
                50
                + _utf16_length(event.title) // 5
                + (10 if event.url.startswith("http") else 0)
                + (20 if enrichment is not None else 0),
            ),
        )
        return PublicSafeSummary(
            internal_summary=internal_summary,
            public_summary=safe_public,
            user_benefit=enrichment_benefit,
            target_audience=target_audience,
            safety_flags=safety_flags,
            confidence=confidence,
            supported_claims=claims,
        )
