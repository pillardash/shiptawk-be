from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.shared.schemas import ApiSchema


class ProductEventName(StrEnum):
    product_created = "product_created"
    github_installation_attached = "github_installation_attached"
    repository_monitoring_enabled = "repository_monitoring_enabled"
    product_profile_approved = "product_profile_approved"
    website_connected = "website_connected"
    website_initial_crawl_completed = "website_initial_crawl_completed"
    search_console_connected = "search_console_connected"
    search_property_selected = "search_property_selected"
    search_baseline_viewed = "search_baseline_viewed"
    weekly_report_generated = "weekly_report_generated"
    weekly_report_viewed = "weekly_report_viewed"
    recommendation_detail_viewed = "recommendation_detail_viewed"
    recommendation_feedback_submitted = "recommendation_feedback_submitted"
    recommendation_accepted = "recommendation_accepted"
    recommendation_dismissed = "recommendation_dismissed"
    asset_edited = "asset_edited"
    asset_approved = "asset_approved"
    asset_rejected = "asset_rejected"
    action_dismissed = "action_dismissed"
    action_started = "action_started"
    action_completed = "action_completed"
    measurement_viewed = "measurement_viewed"
    measurement_completed = "measurement_completed"
    weekly_email_delivered = "weekly_email_delivered"
    compatibility_route_viewed = "compatibility_route_viewed"


class BrowserProductEventName(StrEnum):
    search_baseline_viewed = "search_baseline_viewed"
    weekly_report_viewed = "weekly_report_viewed"
    recommendation_detail_viewed = "recommendation_detail_viewed"
    measurement_viewed = "measurement_viewed"
    compatibility_route_viewed = "compatibility_route_viewed"


class BrowserProductEventCommand(ApiSchema):
    model_config = ConfigDict(
        extra="forbid", alias_generator=ApiSchema.model_config["alias_generator"]
    )
    schema_version: Literal[1] = 1
    event_name: BrowserProductEventName
    resource_id: UUID
    resource_revision: Annotated[int | None, Field(default=None, ge=1)]


class ProductEventReceipt(ApiSchema):
    schema_version: Literal[1] = 1
    event_id: UUID
    event_name: ProductEventName
    duplicate: bool
