from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.modules.evidence.policies.source_urls import is_safe_public_source_url
from app.shared.schemas import ApiSchema

EvidenceText = Annotated[str, StringConstraints(strip_whitespace=True)]


class EvidenceCreate(ApiSchema):
    evidence_type: Literal["manual_note", "url", "customer_quote"]
    product_id: UUID | None = None
    title: EvidenceText = Field(min_length=1, max_length=200)
    content: EvidenceText = Field(min_length=1, max_length=10_000)
    public_safe_summary: EvidenceText | None = Field(default=None, max_length=1000)
    what_happened: EvidenceText | None = Field(default=None, max_length=2000)
    why_it_matters: EvidenceText | None = Field(default=None, max_length=2000)
    source_reference: EvidenceText | None = Field(default=None, max_length=500)
    source_url: EvidenceText | None = Field(default=None, max_length=2048)
    observed_at: datetime
    confidence: Literal["low", "medium", "high"] = "medium"
    classification: Literal["public", "restricted", "confidential", "blocked"] = "public"
    expires_at: datetime | None = None

    @field_validator("source_url")
    @classmethod
    def validate_public_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_safe_public_source_url(value):
            raise ValueError("sourceUrl must be a public HTTP(S) URL without credentials")
        return value

    @model_validator(mode="after")
    def require_type_provenance(self) -> "EvidenceCreate":
        if self.evidence_type == "url" and self.source_url is None:
            raise ValueError("URL evidence requires sourceUrl")
        return self


class EvidenceReviewRequest(ApiSchema):
    reason: EvidenceText | None = Field(default=None, max_length=1000)


class EvidenceRejectRequest(ApiSchema):
    reason: EvidenceText = Field(min_length=1, max_length=1000)


class EvidenceReviewResponse(ApiSchema):
    id: UUID
    decision: Literal["approved", "rejected"]
    reason: str | None
    reviewer_id: UUID
    reviewed_at: datetime


class EvidenceProvenanceResponse(ApiSchema):
    source_type: str
    source_reference: str | None
    source_url: str | None
    observed_at: datetime


class EvidenceResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID | None
    evidence_type: str
    title: str
    content: str | None
    public_safe_summary: str | None
    what_happened: str | None
    why_it_matters: str | None
    confidence: str
    classification: str
    status: str
    freshness_status: Literal["current", "stale", "expired"]
    provenance: EvidenceProvenanceResponse
    last_verified_at: datetime | None
    expires_at: datetime | None
    latest_review: EvidenceReviewResponse | None
    created_at: datetime
    updated_at: datetime


class EvidenceListResponse(ApiSchema):
    items: list[EvidenceResponse]


class LinkedEvidenceResponse(ApiSchema):
    evidence_id: UUID
    evidence_excerpt_id: UUID
    evidence_title: str
    evidence_status: str
    excerpt: str
    locator: str | None


class ClaimResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID | None
    statement: str
    status: str
    last_verified_at: datetime | None
    expires_at: datetime | None
    linked_evidence: list[LinkedEvidenceResponse]
    created_at: datetime
    updated_at: datetime


class ClaimListResponse(ApiSchema):
    items: list[ClaimResponse]
