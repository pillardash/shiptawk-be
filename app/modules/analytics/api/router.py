from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbDep, get_current_user
from app.modules.analytics.repositories.analytics import get_activity, get_summary
from app.modules.analytics.schemas import AnalyticsActivityResponse, AnalyticsSummaryResponse
from app.modules.identity.models.users import User
from app.shared.exceptions import NotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}/analytics", tags=["analytics"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.get("/summary", response_model=AnalyticsSummaryResponse)
async def read_analytics_summary(
    workspace_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> AnalyticsSummaryResponse:
    result = await get_summary(db, workspace_id, current_user.id, days)
    if result is None:
        raise NotFoundError("Analytics workspace not found.", code="analytics_workspace_not_found")
    generated, approval = result
    return AnalyticsSummaryResponse(generated_draft_count=generated, approval=approval)


@router.get("/activity", response_model=AnalyticsActivityResponse)
async def read_analytics_activity(
    workspace_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> AnalyticsActivityResponse:
    timeline = await get_activity(db, workspace_id, current_user.id, days)
    if timeline is None:
        raise NotFoundError("Analytics workspace not found.", code="analytics_workspace_not_found")
    return AnalyticsActivityResponse(status_timeline=timeline)
