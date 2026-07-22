import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

RejectionReason = Literal[
    "missing_evidence",
    "unknown_evidence",
    "unsupported_claim",
    "unsupported_metric",
    "unsupported_absolute",
    "wrong_product",
]

_FLAGS = re.IGNORECASE | re.ASCII
_METRIC_PATTERNS = [
    re.compile(r"\b\d+(?:\.\d+)?\s*%", _FLAGS),
    re.compile(r"\b\d+(?:\.\d+)?\s+percent\b", _FLAGS),
    re.compile(r"[$€£]\s*\d+(?:\.\d+)?", _FLAGS),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:x|times|fold)\b", _FLAGS),
    re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|seconds?|minutes?|hours?|days?|weeks?)\b",
        _FLAGS,
    ),
    re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:users?|customers?|teams?|posts?|drafts?|errors?|bugs?)\b",
        _FLAGS,
    ),
]
_ABSOLUTE_PATTERNS = [
    re.compile(pattern, _FLAGS)
    for pattern in (
        r"\balways\b",
        r"\bnever\b",
        r"\beveryone\b",
        r"\bguaranteed\b",
        r"\bcompletely\b",
        r"\bperfect(?:ly)?\b",
        r"\beliminates?\b",
        r"\bzero\s+(?:errors?|bugs?|downtime|friction)\b",
        r"\b100%\b",
    )
]
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "for",
    "from",
    "in",
    "is",
    "it",
    "now",
    "of",
    "on",
    "the",
    "to",
    "with",
}


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class SupportedClaim(DomainModel):
    id: str
    source: str
    kind: str
    text: str


class PublicSafeSummary(DomainModel):
    internal_summary: str
    public_summary: str
    user_benefit: str
    target_audience: str
    safety_flags: list[str]
    confidence: int
    supported_claims: list[SupportedClaim] | None = None


class GeneratedVariant(DomainModel):
    content: str
    variant: str
    angle: str
    rationale: str
    evidence_ids: list[str] | None = None


class RejectedVariant(DomainModel):
    variant: GeneratedVariant
    reasons: list[RejectionReason]


class EvidenceValidationResult(DomainModel):
    accepted: list[GeneratedVariant]
    rejected: list[RejectedVariant]


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower(), flags=re.ASCII).strip()


def _claim_tokens(value: str) -> set[str]:
    return {
        token for token in _normalize(value).split() if len(token) > 1 and token not in _STOPWORDS
    }


def _has_claim_overlap(content: str, grounding_text: str) -> bool:
    content_tokens = _claim_tokens(content)
    grounding_tokens = _claim_tokens(grounding_text)
    return bool(content_tokens and grounding_tokens and content_tokens & grounding_tokens)


def _matches(content: str, patterns: list[re.Pattern[str]]) -> list[str]:
    return [match.group(0) for pattern in patterns for match in pattern.finditer(content)]


class CandidateEvidenceValidator:
    def validate(
        self,
        *,
        variants: list[GeneratedVariant],
        summary: PublicSafeSummary,
        product_name: str | None,
        subject_name: str,
        repo_name: str,
    ) -> EvidenceValidationResult:
        claims = summary.supported_claims or []
        claim_by_id = {claim.id: claim for claim in claims}
        accepted: list[GeneratedVariant] = []
        rejected: list[RejectedVariant] = []

        for variant in variants:
            reasons: list[RejectionReason] = []
            evidence_ids = variant.evidence_ids or []
            if claims and not evidence_ids:
                reasons.append("missing_evidence")
            if any(evidence_id not in claim_by_id for evidence_id in evidence_ids):
                reasons.append("unknown_evidence")

            cited_text = " ".join(
                claim_by_id[evidence_id].text if evidence_id in claim_by_id else ""
                for evidence_id in evidence_ids
            ).lower()
            grounding_text = " ".join(
                (cited_text, summary.public_summary, summary.user_benefit)
            ).lower()
            if (
                claims
                and evidence_ids
                and "unknown_evidence" not in reasons
                and not _has_claim_overlap(variant.content, grounding_text)
            ):
                reasons.append("unsupported_claim")

            if any(
                match.lower() not in cited_text
                for match in _matches(variant.content, _METRIC_PATTERNS)
            ):
                reasons.append("unsupported_metric")
            if any(
                match.lower() not in cited_text
                for match in _matches(variant.content, _ABSOLUTE_PATTERNS)
            ):
                reasons.append("unsupported_absolute")

            if product_name:
                confirmed = _normalize(product_name)
                aliases = [
                    alias
                    for alias in (
                        _normalize(subject_name),
                        _normalize(repo_name.rsplit("/", maxsplit=1)[-1]),
                    )
                    if alias and alias != confirmed
                ]
                normalized_content = _normalize(variant.content)
                if any(alias in normalized_content for alias in aliases):
                    reasons.append("wrong_product")

            if reasons:
                rejected.append(
                    RejectedVariant(variant=variant, reasons=list(dict.fromkeys(reasons)))
                )
            else:
                accepted.append(variant)

        return EvidenceValidationResult(accepted=accepted, rejected=rejected)
