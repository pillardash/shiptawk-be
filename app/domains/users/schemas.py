from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.core.security import validate_password_policy
from app.shared.schemas import ApiSchema


class UserResponse(ApiSchema):
    id: UUID
    email: EmailStr
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class UserCreate(ApiSchema):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    device_name: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_policy(value)


class UserLogin(ApiSchema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    device_name: str | None = None
