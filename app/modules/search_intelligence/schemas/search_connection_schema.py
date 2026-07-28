from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.modules.search_intelligence.schemas.search_sync_schema import SearchSyncRunResponse
from app.shared.schemas import ApiSchema


class SearchConnectRequest(ApiSchema):
    provider: str
    return_path: str = "/settings"


class SearchAuthorizationResponse(ApiSchema):
    provider: str
    authorization_url: str
    status: Literal["authorization_pending"] = "authorization_pending"


class SearchPropertyRefreshRequest(ApiSchema):
    provider: str = "google"


class SearchPropertyResponse(ApiSchema):
    id: UUID
    provider: str
    provider_property_id: str
    display_name: str
    property_type: str
    permission_level: str
    compatibility_status: str
    last_seen_at: datetime


class SearchPropertySelectionRequest(ApiSchema):
    property_id: UUID


class SearchSourceResponse(ApiSchema):
    id: UUID
    provider: str
    property_id: UUID
    provider_property_id: str
    website_source_id: UUID
    status: str
    selected_at: datetime
    disconnected_at: datetime | None
    latest_successful_data_date: date | None
    initial_sync_run: SearchSyncRunResponse | None = None


class SearchCallbackQuery(ApiSchema):
    state: str = Field(min_length=1)
    code: str | None = None
    error: str | None = None


__all__ = [
    "SearchAuthorizationResponse",
    "SearchCallbackQuery",
    "SearchConnectRequest",
    "SearchPropertyRefreshRequest",
    "SearchPropertyResponse",
    "SearchPropertySelectionRequest",
    "SearchSourceResponse",
]
