import hashlib
import hmac
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notifications.schemas import NotificationPreferences, NotificationPreferencesUpdate
from app.domains.users.models import User


def preferences_for(user: User) -> NotificationPreferences:
    return NotificationPreferences.model_validate(user)


async def update_preferences(
    db: AsyncSession, user: User, update: NotificationPreferencesUpdate
) -> NotificationPreferences:
    for field in update.model_fields_set:
        value = getattr(update, field)
        setattr(user, field, value.value if hasattr(value, "value") else value)
    await db.commit()
    await db.refresh(user)
    return preferences_for(user)


def verify_unsubscribe_token(token: str, secret: str) -> UUID | None:
    try:
        user_id, supplied = token.split(".", maxsplit=1)
        parsed_id = UUID(user_id)
    except (ValueError, AttributeError):
        return None
    expected = hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()
    return parsed_id if hmac.compare_digest(supplied, expected) else None


def create_unsubscribe_token(user_id: UUID, secret: str) -> str:
    user_id_value = str(user_id)
    signature = hmac.new(secret.encode(), user_id_value.encode(), hashlib.sha256).hexdigest()
    return f"{user_id_value}.{signature}"


def unsubscribe_url(*, public_app_url: str, api_prefix: str, user_id: UUID, secret: str) -> str:
    token = create_unsubscribe_token(user_id, secret)
    return (
        f"{public_app_url.rstrip('/')}{api_prefix}/notifications/unsubscribe?"
        f"{urlencode({'token': token})}"
    )
