from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.modules.search_intelligence.enums.search_intelligence_enum import (
    SearchPropertyCompatibility,
    SearchPropertyType,
    SearchProviderKey,
    SearchSourceStatus,
)
from app.modules.search_intelligence.schemas.search_sync_schema import SearchSyncRunResponse
from app.shared.schemas import ApiSchema


class SearchConnectRequest(ApiSchema):
    provider: str = Field(json_schema_extra={"enum": [SearchProviderKey.google.value]})
    return_path: str = "/users/settings/connections"


class SearchAuthorizationResponse(ApiSchema):
    provider: SearchProviderKey
    authorization_url: str
    status: Literal["authorization_pending"] = "authorization_pending"


class SearchPropertyRefreshRequest(ApiSchema):
    provider: str = Field(
        default=SearchProviderKey.google.value,
        json_schema_extra={"enum": [SearchProviderKey.google.value]},
    )


class SearchPropertyResponse(ApiSchema):
    id: UUID
    provider: SearchProviderKey
    provider_property_id: str
    display_name: str
    property_type: SearchPropertyType
    permission_level: str
    compatibility_status: SearchPropertyCompatibility
    last_seen_at: datetime


class SearchPropertySelectionRequest(ApiSchema):
    property_id: UUID


class SearchSourceResponse(ApiSchema):
    id: UUID
    provider: SearchProviderKey
    property_id: UUID
    provider_property_id: str
    website_source_id: UUID
    status: SearchSourceStatus
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
