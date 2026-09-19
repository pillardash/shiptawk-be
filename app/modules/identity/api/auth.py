from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbDep, get_current_user
from app.modules.identity.models.users import User
from app.modules.identity.schemas.oauth import PasswordRegistrationRequest
from app.modules.identity.schemas.users import UserResponse
from app.modules.identity.services.password_auth import register_password_user
from app.shared.responses import MessageResponse

router = APIRouter(prefix="/auth", tags=["auth"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.post("/register", status_code=202, response_model=MessageResponse)
async def register_password_account(
    payload: PasswordRegistrationRequest, db: DbDep
) -> MessageResponse:
    await register_password_user(
        db, name=payload.name, email=str(payload.email), password=payload.password
    )
    return MessageResponse(message="Verification code sent.")


@router.get("/me", response_model=UserResponse)
def me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(current_user)
