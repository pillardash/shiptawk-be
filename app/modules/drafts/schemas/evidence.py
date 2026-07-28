from pydantic import BaseModel, ConfigDict

from app.modules.drafts.enums.generation_enum import RejectionReason


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
