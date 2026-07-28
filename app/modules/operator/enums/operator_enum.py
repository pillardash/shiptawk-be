from enum import StrEnum


class Confidence(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class ApprovalLevel(StrEnum):
    safe = "safe"
    review = "review"
    required = "required"


class OperatorRunKind(StrEnum):
    legacy_planning = "legacy_planning"
    opportunity_detection = "opportunity_detection"


class OperatorRunStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    stopped = "stopped"


class OpportunityType(StrEnum):
    high_impressions_low_ctr = "high_impressions_low_ctr"
    ranking_within_reach = "ranking_within_reach"
    search_decline = "search_decline"
    shipped_but_not_marketed = "shipped_but_not_marketed"
    search_demand_without_dedicated_content = "search_demand_without_dedicated_content"
    existing_page_needs_improvement = "existing_page_needs_improvement"


class ActionFamily(StrEnum):
    seo_snippet_update = "seo_snippet_update"
    existing_page_optimization = "existing_page_optimization"
    content_refresh_investigation = "content_refresh_investigation"
    seo_page_update = "seo_page_update"
    blog_brief = "blog_brief"
    product_update = "product_update"
    dedicated_content_creation = "dedicated_content_creation"
    page_quality_fix = "page_quality_fix"
    x_draft = "x_draft"


class OpportunityStatus(StrEnum):
    open = "open"
    planned = "planned"
    completed = "completed"
    dismissed = "dismissed"
    superseded = "superseded"


class EvaluationOutcome(StrEnum):
    accepted = "accepted"
    no_recommendation = "no_recommendation"
    stopped = "stopped"
    suppressed_duplicate = "suppressed_duplicate"
    suppressed_cooldown = "suppressed_cooldown"
    superseded_in_run = "superseded_in_run"


class EvaluationReason(StrEnum):
    profile_missing = "profile_missing"
    profile_not_approved = "profile_not_approved"
    profile_incomplete = "profile_incomplete"
    search_missing = "search_missing"
    search_unhealthy = "search_unhealthy"
    search_baseline_not_ready = "search_baseline_not_ready"
    search_dates_incomplete = "search_dates_incomplete"
    search_truncated = "search_truncated"
    website_missing = "website_missing"
    website_crawl_failed = "website_crawl_failed"
    website_stale = "website_stale"
    website_profile_mismatch = "website_profile_mismatch"
    shipping_signal_ineligible = "shipping_signal_ineligible"
    sample_too_small = "sample_too_small"
    threshold_not_met = "threshold_not_met"
    target_page_ambiguous = "target_page_ambiguous"
    target_page_missing = "target_page_missing"
    target_page_nonindexable = "target_page_nonindexable"
    navigation_or_branded_query = "navigation_or_branded_query"
    capability_mapping_current = "capability_mapping_current"
    capability_match_insufficient = "capability_match_insufficient"
    event_outside_window = "event_outside_window"
    event_not_public_worthy = "event_not_public_worthy"
    no_page_quality_defect = "no_page_quality_defect"
    score_below_threshold = "score_below_threshold"
    confidence_below_threshold = "confidence_below_threshold"
    strategic_fit_below_threshold = "strategic_fit_below_threshold"
    overlapping_detector_owner = "overlapping_detector_owner"
    insufficient_evidence = "insufficient_evidence"
    stale_data = "stale_data"
    incomplete_data = "incomplete_data"
    contradictory_evidence = "contradictory_evidence"
    duplicate = "duplicate"
    cooldown_active = "cooldown_active"
    lower_priority = "lower_priority"
    unsafe_claim = "unsafe_claim"


class EvidenceSourceKind(StrEnum):
    search = "search"
    website = "website"
    shipping = "shipping"
    product_profile = "product_profile"
    opportunity_history = "opportunity_history"


class FreshnessStatus(StrEnum):
    fresh = "fresh"
    stale = "stale"
    unknown = "unknown"


class FeedbackReason(StrEnum):
    useful = "useful"
    not_relevant = "not_relevant"
    already_done = "already_done"
    insufficient_evidence = "insufficient_evidence"
    incorrect = "incorrect"
    other = "other"
    wrong_priority = "wrong_priority"
    wrong_target = "wrong_target"
    not_actionable = "not_actionable"
    poor_timing = "poor_timing"
    data_incorrect = "data_incorrect"
    not_now = "not_now"
    incorrect_interpretation = "incorrect_interpretation"
    too_much_effort = "too_much_effort"
    duplicate = "duplicate"
    wrong_target_page = "wrong_target_page"


class DismissalReason(StrEnum):
    not_relevant = "not_relevant"
    already_done = "already_done"
    not_now = "not_now"
    insufficient_evidence = "insufficient_evidence"
    incorrect_interpretation = "incorrect_interpretation"
    too_much_effort = "too_much_effort"
    duplicate = "duplicate"
    wrong_target_page = "wrong_target_page"
    other = "other"


class EffortBand(StrEnum):
    small = "small"
    medium = "medium"
    large = "large"
