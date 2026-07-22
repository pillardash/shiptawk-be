from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.domains.users.models import User
from app.domains.users.schemas import UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.get("/me", response_model=UserResponse)
def me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(current_user)
