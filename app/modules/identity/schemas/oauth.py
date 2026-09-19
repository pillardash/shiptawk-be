from uuid import UUID

from pydantic import EmailStr, Field

from app.modules.identity.schemas.users import Trimmed120, UserResponse
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


class PasswordRegistrationRequest(ApiSchema):
    name: Trimmed120
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class EmailVerificationRequest(ApiSchema):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class PasswordLoginRequest(ApiSchema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
