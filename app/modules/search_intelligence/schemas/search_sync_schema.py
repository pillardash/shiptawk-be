from datetime import date, datetime
from typing import Literal
from uuid import UUID

from app.shared.schemas import ApiSchema


class SearchSyncRequest(ApiSchema):
    trigger: Literal["manual", "scheduled"] = "manual"


class SearchSyncRunResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    source_id: UUID
    trigger: str
    status: str
    requested_start_date: date
    requested_end_date: date
    progress_date: date | None
    policy_version: str
    schema_version: int
    rows_received: int
    rows_written: int
    is_complete: bool
    is_truncated: bool
    failure_category: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


__all__ = ["SearchSyncRequest", "SearchSyncRunResponse"]
