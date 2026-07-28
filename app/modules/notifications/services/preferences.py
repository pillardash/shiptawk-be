from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models.users import User
from app.modules.notifications.repositories.notifications import disable_email_notifications
from app.modules.notifications.schemas import NotificationPreferences, NotificationPreferencesUpdate


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


async def unsubscribe_user(db: AsyncSession, user_id: UUID) -> bool:
    disabled = await disable_email_notifications(db, user_id)
    if disabled:
        await db.commit()
    return disabled
