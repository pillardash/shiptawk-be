from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import field_validator

from app.shared.schemas import ApiSchema


class InstallationAttachRequest(ApiSchema):
    installation_id: int


class InstallationResponse(ApiSchema):
    installation_id: int
    account_login: str
    status: str


class InstallationStatusResponse(ApiSchema):
    connected: bool
    status: str | None
    account_login: str | None
    installation_id: int | None


class XConnectionStatusResponse(ApiSchema):
    connected: bool
    status: str | None
    account_id: str | None
    username: str | None
    scopes: list[str]
    token_expires_at: datetime | None


class IntegrationConnectionResponse(ApiSchema):
    id: UUID
    provider: str
    account_id: str
    username: str | None
    status: str
    scopes: list[str]
    token_expires_at: datetime | None
    connected_at: datetime


class RepositoryResponse(ApiSchema):
    id: UUID
    workspace_id: UUID
    repo_name: str
    repo_full_name: str
    private: bool
    tracked_branch: str | None
    language: str | None
    description: str | None
    is_tracked: bool
    last_checked_at: datetime | None
    changelog_digest_enabled: bool
    changelog_digest_frequency: Literal["weekly", "monthly"]
    created_at: datetime


class TrackingUpdateRequest(ApiSchema):
    is_tracked: bool


class RepositoryDetailUpdateRequest(ApiSchema):
    tracked_branch: str | None

    @field_validator("tracked_branch")
    @classmethod
    def validate_tracked_branch(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("trackedBranch must not be blank")
        return value


class ChangelogDigestSettingsUpdateRequest(ApiSchema):
    enabled: bool
    frequency: Literal["weekly", "monthly"]
