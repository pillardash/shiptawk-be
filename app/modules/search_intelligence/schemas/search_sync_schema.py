from datetime import date, datetime
from uuid import UUID

from pydantic import Field

from app.modules.search_intelligence.enums.search_intelligence_enum import (
    SearchSyncStatus,
    SearchSyncTrigger,
)
from app.shared.schemas import ApiSchema


class SearchSyncRequest(ApiSchema):
    trigger: str = Field(
        default=SearchSyncTrigger.manual.value,
        json_schema_extra={"enum": [SearchSyncTrigger.manual.value]},
    )


class SearchSyncRunResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    source_id: UUID
    trigger: SearchSyncTrigger
    status: SearchSyncStatus
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


class SearchSyncRunListResponse(ApiSchema):
    items: list[SearchSyncRunResponse]


__all__ = ["SearchSyncRequest", "SearchSyncRunListResponse", "SearchSyncRunResponse"]
