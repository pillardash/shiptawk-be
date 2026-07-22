from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.api.deps import (
    DbDep,
    TokenDep,
    get_browser_session,
    get_current_user,
    require_cookie_auth_csrf,
)
from app.core.config import get_settings
from app.domains.notifications.repository import disable_email_notifications, unread_count
from app.domains.notifications.schemas import (
    NotificationPreferences,
    NotificationPreferencesUpdate,
    UnreadCountResponse,
)
from app.domains.notifications.service import (
    preferences_for,
    update_preferences,
    verify_unsubscribe_token,
)
from app.domains.users.models import User
from app.shared.exceptions import NotFoundError, ServiceUnavailableError

router = APIRouter(tags=["notifications"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def authorize_preferences_update(request: Request, token: TokenDep, db: DbDep) -> User:
    if token is not None:
        return await get_current_user(request, token, db)
    return (await require_cookie_auth_csrf(request, await get_browser_session(request, db)))[0]


PreferencesUpdateUserDep = Annotated[User, Depends(authorize_preferences_update)]


@router.get(
    "/workspaces/{workspace_id}/notifications/unread-count",
    response_model=UnreadCountResponse,
)
async def read_unread_count(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> UnreadCountResponse:
    count = await unread_count(
        db, workspace_id, current_user.id, current_user.last_dashboard_viewed_at
    )
    if count is None:
        raise NotFoundError(
            "Notification workspace not found.", code="notification_workspace_not_found"
        )
    return UnreadCountResponse(count=count)


@router.get("/notifications/preferences", response_model=NotificationPreferences)
def read_notification_preferences(current_user: CurrentUserDep) -> NotificationPreferences:
    return preferences_for(current_user)


@router.patch("/notifications/preferences", response_model=NotificationPreferences)
async def patch_notification_preferences(
    payload: NotificationPreferencesUpdate,
    current_user: PreferencesUpdateUserDep,
    db: DbDep,
) -> NotificationPreferences:
    return await update_preferences(db, current_user, payload)


@router.get("/notifications/unsubscribe", response_model=None)
async def unsubscribe(
    token: Annotated[str, Query(min_length=1)], db: DbDep
) -> HTMLResponse | PlainTextResponse:
    secret = get_settings().token_encryption_key
    if secret is None:
        raise ServiceUnavailableError(
            "Notification unsubscribe is not configured.", code="unsubscribe_not_configured"
        )
    user_id = verify_unsubscribe_token(token, secret.get_secret_value())
    if user_id is None:
        return PlainTextResponse("Invalid unsubscribe link.", status_code=400)
    if not await disable_email_notifications(db, user_id):
        return PlainTextResponse("Invalid unsubscribe link.", status_code=400)
    return HTMLResponse(
        "<html><body><p>You've been unsubscribed from Shiptawk email notifications. "
        "You can re-enable them in your settings.</p></body></html>"
    )
