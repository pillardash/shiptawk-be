from uuid import UUID

from app.modules.identity.schemas.users import UserResponse
from app.shared.schemas import ApiSchema


class OAuthProviderResponse(ApiSchema):
    name: str
    display_name: str


class CurrentWorkspaceResponse(ApiSchema):
    id: UUID
    name: str
    role: str


class PrimaryIdentityResponse(ApiSchema):
    provider: str
    username: str | None
    display_name: str | None
    avatar_url: str | None


class BrowserSessionResponse(ApiSchema):
    user: UserResponse
    current_workspace: CurrentWorkspaceResponse
    linked_providers: list[str]
    primary_identity: PrimaryIdentityResponse | None


class BrowserWorkspaceUpdate(ApiSchema):
    workspace_id: UUID
