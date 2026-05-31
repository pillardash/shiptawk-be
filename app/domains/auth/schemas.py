from pydantic import EmailStr, Field, field_validator

from app.core.security import validate_password_policy
from app.domains.users.schemas import UserResponse
from app.shared.schemas import ApiSchema


class AuthResponse(ApiSchema):
    user: UserResponse
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(ApiSchema):
    refresh_token: str
    device_name: str | None = None


class LogoutRequest(ApiSchema):
    refresh_token: str


class ChangePasswordRequest(ApiSchema):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_policy(value)


class EmailRequest(ApiSchema):
    email: EmailStr


class ResetPasswordRequest(ApiSchema):
    token: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password_policy(value)


class VerifyEmailRequest(ApiSchema):
    token: str
