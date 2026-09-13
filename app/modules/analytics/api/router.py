from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from app.api.deps import DbDep, MutationUserDep, get_current_user
from app.modules.analytics.repositories.analytics import get_activity, get_summary
from app.modules.analytics.schemas import (
    AnalyticsActivityResponse,
    AnalyticsSummaryResponse,
    BrowserProductEventCommand,
    ProductEventReceipt,
)
from app.modules.analytics.services.product_event_service import (
    BROWSER_EVENT_RESOURCE_TYPES,
    EventIdempotencyConflictError,
    browser_event_resource_exists,
    record_product_event,
)
from app.modules.identity.models.users import User
from app.modules.workspaces.services import require_active_workspace_role
from app.shared.exceptions import ConflictError, NotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}/analytics", tags=["analytics"])
browser_event_router = APIRouter(
    prefix="/workspaces/{workspace_id}/products/{product_id}/analytics", tags=["product-analytics"]
)
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


@browser_event_router.post("/events", response_model=ProductEventReceipt)
async def record_browser_product_event(
    workspace_id: UUID,
    product_id: UUID,
    payload: BrowserProductEventCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> ProductEventReceipt:
    await require_active_workspace_role(
        db,
        workspace_id,
        user.id,
        missing=NotFoundError("Product not found.", code="product_not_found"),
    )
    event_name = payload.event_name.value
    if not await browser_event_resource_exists(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        event_name=event_name,
        resource_id=payload.resource_id,
    ):
        raise NotFoundError("Event resource not found.", code="event_resource_not_found")
    # Weekly report retention is only valid through the canonical plan-view endpoint.
    if event_name == "weekly_report_viewed":
        raise ConflictError(
            "Use the plan view endpoint for weekly reports.", code="canonical_plan_view_required"
        )
    try:
        event, duplicate = await record_product_event(
            db=db,
            workspace_id=workspace_id,
            product_id=product_id,
            actor_id=user.id,
            event_name=event_name,
            idempotency_key=idempotency_key,
            resource_type=BROWSER_EVENT_RESOURCE_TYPES[event_name],
            resource_id=payload.resource_id,
            resource_revision=payload.resource_revision,
        )
    except EventIdempotencyConflictError as exc:
        raise ConflictError(
            "Idempotency key was reused with a different event.",
            code="idempotency_payload_conflict",
        ) from exc
    return ProductEventReceipt(event_id=event.id, event_name=event.event_name, duplicate=duplicate)
