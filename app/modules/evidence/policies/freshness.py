from datetime import UTC, datetime
from typing import Literal

from app.modules.evidence.models import EvidenceItem


def evidence_freshness(
    item: EvidenceItem, *, now: datetime | None = None
) -> Literal["current", "stale", "expired"]:
    evaluated_at = now or datetime.now(UTC)
    expires_at = item.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= evaluated_at:
            return "expired"
    if item.status in {"stale", "contradicted"}:
        return "stale"
    return "current"
