from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import UUID

from app.domains.evidence.models import EvidenceItem


@dataclass(frozen=True)
class AnalyzedRecommendation:
    title: str
    evidence_ids: list[UUID]
    interpretation: str
    recommendation: str
    confidence: str
    missing_information: list[str]
    approval_level: str
    measurement_window: str
    stop_conditions: list[str]
    impact_score: int
    effort_score: int
    urgency_score: int
    cooldown_dedup_key: str


class OpportunityAnalyzer(Protocol):
    version: str
    provenance: dict[str, object]

    def analyze(self, evidence: list[EvidenceItem]) -> list[AnalyzedRecommendation]: ...


class ApprovedEvidenceAnalyzer:
    """Deterministic baseline analyzer; it performs no network or model calls."""

    version = "approved-evidence-v1"
    provenance: ClassVar[dict[str, object]] = {
        "analyzer": "approved-evidence",
        "version": "1",
        "mode": "deterministic",
    }

    def analyze(self, evidence: list[EvidenceItem]) -> list[AnalyzedRecommendation]:
        recommendations = []
        for item in evidence[:3]:
            recommendations.append(
                AnalyzedRecommendation(
                    title=f"Act on: {item.title}",
                    evidence_ids=[item.id],
                    interpretation=item.why_it_matters
                    or "Approved evidence identifies a bounded marketing improvement.",
                    recommendation=f"Prepare a reviewed action for {item.title.lower()}.",
                    confidence=item.confidence,
                    missing_information=[],
                    approval_level="review",
                    measurement_window="28 days",
                    stop_conditions=["Evidence becomes stale or contradicted"],
                    impact_score=70,
                    effort_score=30,
                    urgency_score=50,
                    cooldown_dedup_key=f"evidence:{item.id}",
                )
            )
        return recommendations
