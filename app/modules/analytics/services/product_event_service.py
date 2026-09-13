import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.models import ProductEvent
from app.modules.analytics.repositories.product_event_repository import (
    add_event,
    get_event_by_actor_key,
)
from app.modules.integrations.models import IntegrationConnection
from app.modules.operator.models import MarketingPlan, MeasurementWindow, OperatorRecommendation
from app.modules.products.models import Product
from app.modules.repos.models import repos
from app.modules.search_intelligence.models import SearchSource


class EventIdempotencyConflictError(Exception):
    pass


class EventScopeError(ValueError):
    pass


WORKSPACE_EVENT_RESOURCES = {
    "github_installation_attached": "integration_connection",
    "repository_monitoring_enabled": "repository",
}

BROWSER_EVENT_RESOURCE_TYPES = {
    "search_baseline_viewed": "search_baseline",
    "weekly_report_viewed": "weekly_report",
    "recommendation_detail_viewed": "recommendation",
    "measurement_viewed": "measurement",
    "compatibility_route_viewed": "compatibility_route",
}


async def browser_event_resource_exists(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    event_name: str,
    resource_id: UUID,
) -> bool:
    if event_name == "search_baseline_viewed":
        if resource_id != product_id:
            return False
        statement = select(SearchSource.id).where(
            SearchSource.workspace_id == workspace_id,
            SearchSource.product_id == product_id,
            SearchSource.status == "active",
        )
    elif event_name == "weekly_report_viewed":
        statement = select(MarketingPlan.id).where(
            MarketingPlan.workspace_id == workspace_id,
            MarketingPlan.product_id == product_id,
            MarketingPlan.id == resource_id,
        )
    elif event_name == "recommendation_detail_viewed":
        statement = select(OperatorRecommendation.id).where(
            OperatorRecommendation.workspace_id == workspace_id,
            OperatorRecommendation.product_id == product_id,
            OperatorRecommendation.id == resource_id,
        )
    elif event_name == "measurement_viewed":
        statement = select(MeasurementWindow.id).where(
            MeasurementWindow.workspace_id == workspace_id,
            MeasurementWindow.product_id == product_id,
            MeasurementWindow.id == resource_id,
        )
    elif event_name == "compatibility_route_viewed":
        if resource_id != product_id:
            return False
        statement = select(Product.id).where(
            Product.workspace_id == workspace_id,
            Product.id == product_id,
        )
    else:
        return False
    return await db.scalar(statement) is not None


def event_fingerprint(
    *,
    event_name: str,
    product_id: UUID | None,
    resource_type: str | None,
    resource_id: UUID | None,
    resource_revision: int | None,
) -> str:
    payload = {
        "eventName": event_name,
        "productId": str(product_id),
        "resourceType": resource_type,
        "resourceId": str(resource_id) if resource_id else None,
        "resourceRevision": resource_revision,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def write_product_event(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID | None,
    actor_id: UUID | None,
    event_name: str,
    idempotency_key: str,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    resource_revision: int | None = None,
    report_ordinal: int | None = None,
    second_report_return: bool | None = None,
    fourth_report_return: bool | None = None,
    valid_no_recommendation_report: bool | None = None,
) -> tuple[ProductEvent, bool]:
    expected_resource_type = WORKSPACE_EVENT_RESOURCES.get(event_name)
    if expected_resource_type is None:
        if product_id is None:
            raise EventScopeError("Product-scoped events require a product ID.")
    else:
        if product_id is not None:
            raise EventScopeError("Workspace-scoped events cannot have a product ID.")
        if resource_type != expected_resource_type or resource_id is None:
            raise EventScopeError("Workspace-scoped event resource type or ID is invalid.")
        if event_name == "github_installation_attached":
            owned_resource = await db.scalar(
                select(IntegrationConnection.id).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.id == resource_id,
                    IntegrationConnection.provider == "github_app",
                )
            )
        else:
            owned_resource = await db.scalar(
                select(repos.c.id).where(
                    repos.c.workspace_id == workspace_id,
                    repos.c.id == resource_id,
                    repos.c.is_tracked.is_(True),
                    repos.c.tracking_generation == resource_revision,
                )
            )
        if owned_resource is None:
            raise EventScopeError("Workspace-scoped event resource was not found.")
    fingerprint = event_fingerprint(
        event_name=event_name,
        product_id=product_id,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_revision=resource_revision,
    )
    existing = await get_event_by_actor_key(
        db, workspace_id=workspace_id, actor_id=actor_id, idempotency_key=idempotency_key
    )
    if existing:
        if existing.payload_fingerprint != fingerprint:
            raise EventIdempotencyConflictError
        return existing, True
    return await add_event(
        db,
        ProductEvent(
            workspace_id=workspace_id,
            product_id=product_id,
            actor_id=actor_id,
            event_name=event_name,
            idempotency_key=idempotency_key,
            payload_fingerprint=fingerprint,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_revision=resource_revision,
            report_ordinal=report_ordinal,
            second_report_return=second_report_return,
            fourth_report_return=fourth_report_return,
            valid_no_recommendation_report=valid_no_recommendation_report,
        ),
    ), False


async def record_product_event(**kwargs: object) -> tuple[ProductEvent, bool]:
    db = kwargs.pop("db")
    assert isinstance(db, AsyncSession)
    event, duplicate = await write_product_event(db, **kwargs)  # type: ignore[arg-type]
    await db.commit()
    return event, duplicate
