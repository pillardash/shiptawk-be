from collections.abc import Callable

from app.modules.operator.detectors import (
    existing_page_needs_improvement_detector,
    high_impressions_low_ctr_detector,
    ranking_within_reach_detector,
    search_decline_detector,
    search_demand_without_dedicated_content_detector,
    shipped_but_not_marketed_detector,
)
from app.modules.operator.schemas.opportunity_detection_schema import (
    DetectionInput,
    DetectorEvaluation,
)

Detector = Callable[[DetectionInput], tuple[DetectorEvaluation, ...]]
DETECTOR_REGISTRY: tuple[tuple[str, str, Detector], ...] = tuple(
    (module.DETECTOR_ID, module.DETECTOR_VERSION, module.detect)
    for module in (
        high_impressions_low_ctr_detector,
        ranking_within_reach_detector,
        search_decline_detector,
        shipped_but_not_marketed_detector,
        search_demand_without_dedicated_content_detector,
        existing_page_needs_improvement_detector,
    )
)


def validate_registry(registry: tuple[tuple[str, str, Detector], ...] = DETECTOR_REGISTRY) -> None:
    if len(registry) != 6:
        raise ValueError("The detector registry must contain exactly six detectors.")
    identities = [(item[0], item[1]) for item in registry]
    if len(identities) != len(set(identities)):
        raise ValueError("Detector IDs and versions must be unique.")


validate_registry()
