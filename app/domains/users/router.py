from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    BrowserSessionDep,
    CookieAuthCsrfDep,
    DbDep,
    TokenDep,
    get_browser_session,
    get_current_user,
    require_cookie_auth_csrf,
)
from app.domains.users.models import User
from app.domains.users.schemas import (
    UserGenerationSettingsResponse,
    UserGenerationSettingsUpdate,
    UserOnboardingStatusResponse,
    UserProfileResponse,
)
from app.domains.users.service import (
    complete_onboarding,
    get_generation_settings,
    get_onboarding_status,
    get_session_workspace_id,
    get_user_profile,
    record_dashboard_view,
    update_generation_settings,
)

router = APIRouter(prefix="/user", tags=["current-user"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def authorize_settings_update(request: Request, token: TokenDep, db: DbDep) -> User:
    if token is not None:
        return await get_current_user(request, token, db)
    return (await require_cookie_auth_csrf(request, await get_browser_session(request, db)))[0]


SettingsUpdateUserDep = Annotated[User, Depends(authorize_settings_update)]


@router.get("/profile", response_model=UserProfileResponse)
async def read_user_profile(current_user: CurrentUserDep, db: DbDep) -> UserProfileResponse:
    return await get_user_profile(db, current_user)


@router.get("/onboarding", response_model=UserOnboardingStatusResponse)
async def read_user_onboarding(
    browser_session: BrowserSessionDep, db: DbDep
) -> UserOnboardingStatusResponse:
    user, session_id = browser_session
    workspace_id = await get_session_workspace_id(db, user_id=user.id, session_id=session_id)
    return await get_onboarding_status(db, user=user, workspace_id=workspace_id)


@router.post("/onboarding/complete", response_model=UserOnboardingStatusResponse)
async def finish_user_onboarding(
    request: Request, principal: CookieAuthCsrfDep, db: DbDep
) -> UserOnboardingStatusResponse:
    user, session_id = principal
    workspace_id = await get_session_workspace_id(db, user_id=user.id, session_id=session_id)
    return await complete_onboarding(
        db,
        user=user,
        workspace_id=workspace_id,
        trigger=getattr(request.app.state, "onboarding_generation_trigger", None),
    )


@router.post("/dashboard-view", status_code=status.HTTP_204_NO_CONTENT)
async def dashboard_viewed(principal: CookieAuthCsrfDep, db: DbDep) -> Response:
    await record_dashboard_view(db, principal[0])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/settings", response_model=UserGenerationSettingsResponse)
def read_user_generation_settings(
    current_user: CurrentUserDep,
) -> UserGenerationSettingsResponse:
    return get_generation_settings(current_user)


@router.patch("/settings", response_model=UserGenerationSettingsResponse)
async def patch_user_generation_settings(
    settings: UserGenerationSettingsUpdate,
    current_user: SettingsUpdateUserDep,
    db: DbDep,
) -> UserGenerationSettingsResponse:
    return await update_generation_settings(db, current_user, settings)
