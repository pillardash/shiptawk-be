from decimal import Decimal

from app.modules.operator.enums import ActionFamily
from app.modules.operator.schemas.opportunity_detection_schema import MetricSnapshot

DETECTOR_SCORE_POLICY_VERSION = "v1"

EFFORT = {
    ActionFamily.seo_snippet_update: Decimal(20),
    ActionFamily.existing_page_optimization: Decimal(50),
    ActionFamily.content_refresh_investigation: Decimal(50),
    ActionFamily.product_update: Decimal(35),
    ActionFamily.dedicated_content_creation: Decimal(75),
    ActionFamily.page_quality_fix: Decimal(45),
}


def search_scores(
    metrics: MetricSnapshot,
    action: ActionFamily,
    *,
    relevance: Decimal = Decimal(".5"),
    signal: Decimal = Decimal(0),
) -> dict[str, Decimal]:
    sample = min(Decimal(100), Decimal(60) + Decimal(metrics.impressions) / 10)
    impact = min(Decimal(100), Decimal(45) + Decimal(metrics.impressions) / 20 + signal)
    urgency = min(Decimal(100), Decimal(40) + signal)
    return {
        "impact": impact,
        "confidence": sample,
        "fit": min(Decimal(100), Decimal(55) + relevance * 45),
        "urgency": urgency,
        "effort": EFFORT[action],
    }


def shipping_scores(
    worthiness: Decimal, capability_importance: Decimal, action: ActionFamily
) -> dict[str, Decimal]:
    return {
        "impact": min(Decimal(100), worthiness * Decimal(".7") + capability_importance * 30),
        "confidence": min(Decimal(100), Decimal(65) + capability_importance * 30),
        "fit": min(Decimal(100), Decimal(60) + capability_importance * 40),
        "urgency": Decimal(75),
        "effort": EFFORT[action],
    }
