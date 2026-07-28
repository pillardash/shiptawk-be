from enum import StrEnum


class EvidenceType(StrEnum):
    manual_note = "manual_note"
    url = "url"
    customer_quote = "customer_quote"


class EvidenceStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    stale = "stale"
    contradicted = "contradicted"


class EvidenceClassification(StrEnum):
    public = "public"
    restricted = "restricted"
    confidential = "confidential"
    blocked = "blocked"


class ClaimStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    rejected = "rejected"
    stale = "stale"
    contradicted = "contradicted"


__all__ = ["ClaimStatus", "EvidenceClassification", "EvidenceStatus", "EvidenceType"]
