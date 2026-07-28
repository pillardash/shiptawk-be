from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import cast

from app.modules.operator.enums import EvaluationReason
from app.modules.operator.schemas.opportunity_detection_schema import (
    DetectionInput,
    ShippingEventSnapshot,
)

QUALITY_POLICY_VERSION = "v1"


def quality_reasons(
    data: DetectionInput, *, requires_search: bool = False, requires_website: bool = False
) -> tuple[EvaluationReason, ...]:
    reasons: list[EvaluationReason] = []
    if data.profile.status != "approved":
        reasons.append(EvaluationReason.profile_not_approved)
    if data.profile.completeness < 100:
        reasons.append(EvaluationReason.profile_incomplete)
    if requires_search:
        search = data.search
        if search is None:
            reasons.append(EvaluationReason.search_missing)
        else:
            if search.health_status != "healthy":
                reasons.append(EvaluationReason.search_unhealthy)
            if search.baseline_status != "ready":
                reasons.append(EvaluationReason.search_baseline_not_ready)
            if (
                not search.complete
                or search.missing_dates
                or _period_days(search) != 56
                or not _periods_are_adjacent(search)
            ):
                reasons.append(EvaluationReason.search_dates_incomplete)
            if search.truncated:
                reasons.append(EvaluationReason.search_truncated)
    if requires_website:
        website = data.website
        if website is None:
            reasons.append(EvaluationReason.website_missing)
        else:
            if website.status != "succeeded" or not website.complete:
                reasons.append(EvaluationReason.website_crawl_failed)
            if (
                website.completed_at is None
                or website.completed_at.tzinfo is None
                or data.as_of.tzinfo is None
                or website.completed_at > data.as_of
                or data.as_of - website.completed_at > timedelta(days=7)
            ):
                reasons.append(EvaluationReason.website_stale)
            if website.profile_version != data.profile.profile_version:
                reasons.append(EvaluationReason.website_profile_mismatch)
    return tuple(dict.fromkeys(reasons))


def _period_days(data: object) -> int:
    search = data
    starts = (getattr(search, "current_start", None), getattr(search, "prior_start", None))
    ends = (getattr(search, "current_end", None), getattr(search, "prior_end", None))
    if not all((*starts, *ends)):
        return 0
    typed_starts = cast(tuple[date, date], starts)
    typed_ends = cast(tuple[date, date], ends)
    return sum((end - start).days + 1 for start, end in zip(typed_starts, typed_ends, strict=True))


def _periods_are_adjacent(data: object) -> bool:
    current_start = getattr(data, "current_start", None)
    prior_end = getattr(data, "prior_end", None)
    return bool(current_start and prior_end and prior_end + timedelta(days=1) == current_start)


def shipping_signal_reasons(
    event: ShippingEventSnapshot, as_of: datetime
) -> tuple[EvaluationReason, ...]:
    reasons = []
    if event.occurred_at > as_of or as_of - event.occurred_at > timedelta(days=28):
        reasons.append(EvaluationReason.event_outside_window)
    if event.evaluation_outcome != "generate":
        reasons.append(EvaluationReason.shipping_signal_ineligible)
    if event.public_worthiness_score < 66:
        reasons.append(EvaluationReason.event_not_public_worthy)
    if event.capability_coverage_score < Decimal(
        "0.60"
    ) or event.capability_relevance_score < Decimal("0.20"):
        reasons.append(EvaluationReason.capability_match_insufficient)
    return tuple(reasons)
