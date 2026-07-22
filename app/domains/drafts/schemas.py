from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

from app.shared.schemas import ApiSchema


class DraftStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    posted = "posted"
    rejected = "rejected"


class DraftContentUpdate(ApiSchema):
    content: Annotated[str, Field(min_length=1, max_length=280)]


class DraftRejection(ApiSchema):
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=280),
    ]


class DraftCandidateSelection(ApiSchema):
    candidate_id: UUID


class DraftAngleRegeneration(ApiSchema):
    angle: Annotated[
        str,
        Field(
            pattern=(
                "^(user_benefit|problem_solved|shipping_update|technical_credibility|"
                "founder_build_in_public|milestone_or_traction)$"
            )
        ),
    ]
    idempotency_key: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]


class DraftPublishResponse(ApiSchema):
    draft_id: UUID
    provider: str
    provider_post_id: str
    url: str
    idempotency_key: str
    already_posted: bool


class DraftResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    repo_id: UUID
    repo_full_name: str | None = None
    normalized_event_id: UUID | None
    content: str
    user_edited: bool
    edit_distance: int | None
    source_event_type: str
    status: DraftStatus
    rejection_reason: str | None
    approved_at: datetime | None
    rejected_at: datetime | None
    posted_at: datetime | None
    tweet_id: str | None
    created_at: datetime
    updated_at: datetime
    generation_run_id: UUID | None
    selected_candidate_id: UUID | None


class EventEvaluationResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    normalized_event_id: UUID
    repo_id: UUID
    public_worthiness_score: int
    outcome: str
    dimension_scores: dict[str, int]
    is_public_worthy: bool
    failed_safety_checks: list[str]
    rationale: str
    public_summary: str | None
    user_benefit: str | None
    target_audience: str | None
    confidence: int | None
    created_at: datetime
    updated_at: datetime


class TweetCandidateResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    normalized_event_id: UUID
    repo_id: UUID
    content: str
    angle: str
    variant: str
    dimension_scores: dict[str, int]
    rank: int
    score: int
    rationale: str
    created_at: datetime


class PaginationResponse(ApiSchema):
    page: int
    limit: int
    total: int
    total_pages: int
    has_next: bool
    has_prev: bool


class DraftListResponse(ApiSchema):
    data: list[DraftResponse]
    pagination: PaginationResponse


class DraftReviewData(ApiSchema):
    draft: DraftResponse
    evaluation: EventEvaluationResponse | None
    candidates: list[TweetCandidateResponse]


class DraftReviewResponse(ApiSchema):
    data: DraftReviewData
