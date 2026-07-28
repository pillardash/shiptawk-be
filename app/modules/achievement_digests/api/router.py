from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    DbDep,
    MutationUserDep,
    get_current_user,
)
from app.modules.achievement_digests.repositories.digests import (
    list_recent_digests,
)
from app.modules.achievement_digests.schemas import (
    AchievementDigestFeedbackCreate,
    AchievementDigestFeedbackResponse,
    AchievementDigestListResponse,
    AchievementDigestResponse,
)
from app.modules.achievement_digests.services.feedback import record_digest_feedback
from app.modules.identity.models.users import User
from app.shared.exceptions import NotFoundError

router = APIRouter(
    prefix="/workspaces/{workspace_id}/achievement-digests", tags=["achievement-digests"]
)
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=AchievementDigestListResponse)
async def list_achievement_digests(
    workspace_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> AchievementDigestListResponse:
    rows = await list_recent_digests(db, workspace_id, current_user.id, limit)
    if rows is None:
        raise NotFoundError(
            "Achievement digest workspace not found.", code="digest_workspace_not_found"
        )
    return AchievementDigestListResponse(
        data=[AchievementDigestResponse.model_validate(row) for row in rows]
    )


@router.post(
    "/{digest_id}/feedback",
    response_model=AchievementDigestFeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_achievement_digest_feedback(
    workspace_id: UUID,
    digest_id: UUID,
    payload: AchievementDigestFeedbackCreate,
    current_user: MutationUserDep,
    db: DbDep,
) -> AchievementDigestFeedbackResponse:
    row = await record_digest_feedback(
        db,
        workspace_id,
        current_user.id,
        digest_id,
        payload.action,
        payload.metadata,
    )
    if row is None:
        raise NotFoundError("Achievement digest not found.", code="achievement_digest_not_found")
    return AchievementDigestFeedbackResponse.model_validate(row)
