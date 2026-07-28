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


class BrowserSessionResponse(ApiSchema):
    user: UserResponse
    current_workspace: CurrentWorkspaceResponse
    linked_providers: list[str]
