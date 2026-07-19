from datetime import datetime
from uuid import UUID

from app.shared.schemas import ApiSchema


class ProductResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    target_audience: str | None
    messaging_angle: str | None
    tone_override: str | None
    blocked_terms: list[str]
    blocked_topics: list[str]
    safe_public_boundaries: list[str]
    website_url: str | None
    primary_customer_pain: str | None
    desired_outcome: str | None
    positioning_statement: str | None
    proof_points: list[str]
    customer_use_cases: list[str]
    content_goal: str
    cta_preference: str | None
    founder_story_angle: str | None
    created_at: datetime
    updated_at: datetime
