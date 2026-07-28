from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.api.deps import (
    DbDep,
    MutationUserDep,
    get_current_user,
)
from app.core.config import get_settings
from app.modules.identity.models.users import User
from app.modules.notifications.policies.unsubscribe import verify_unsubscribe_token
from app.modules.notifications.repositories.notifications import unread_count
from app.modules.notifications.schemas import (
    NotificationPreferences,
    NotificationPreferencesUpdate,
    UnreadCountResponse,
)
from app.modules.notifications.services.preferences import (
    preferences_for,
    unsubscribe_user,
    update_preferences,
)
from app.shared.exceptions import NotFoundError, ServiceUnavailableError

router = APIRouter(tags=["notifications"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


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
    current_user: MutationUserDep,
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
    if not await unsubscribe_user(db, user_id):
        return PlainTextResponse("Invalid unsubscribe link.", status_code=400)
    return HTMLResponse(
        "<html><body><p>You've been unsubscribed from Shiptawk email notifications. "
        "You can re-enable them in your settings.</p></body></html>"
    )
