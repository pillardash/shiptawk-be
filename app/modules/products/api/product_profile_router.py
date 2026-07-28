from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbDep, MutationUserDep, get_current_user
from app.modules.identity.models.users import User
from app.modules.products.schemas.product_profile_schema import (
    ProductProfileApproveRequest,
    ProductProfileAuditEventResponse,
    ProductProfileCurrentResponse,
    ProductProfileDraftUpdate,
    ProductProfileHistoryResponse,
    ProductProfileVersionResponse,
)
from app.modules.products.services.product_profile_service import (
    approve_draft,
    profile_audit_events,
    profile_version,
    profile_versions,
    read_product_profile,
    save_draft,
)

product_profile_router = APIRouter(
    prefix="/{product_id}/product-profile",
    tags=["product-profile"],
)
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@product_profile_router.get("", response_model=ProductProfileCurrentResponse)
async def get_product_profile(
    workspace_id: UUID, product_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> ProductProfileCurrentResponse:
    draft, approved = await read_product_profile(db, workspace_id, product_id, current_user.id)
    return ProductProfileCurrentResponse(
        draft=ProductProfileVersionResponse.model_validate(draft) if draft else None,
        approved=ProductProfileVersionResponse.model_validate(approved) if approved else None,
    )


@product_profile_router.put("", response_model=ProductProfileVersionResponse)
async def update_product_profile(
    workspace_id: UUID,
    product_id: UUID,
    payload: ProductProfileDraftUpdate,
    current_user: MutationUserDep,
    db: DbDep,
) -> ProductProfileVersionResponse:
    return ProductProfileVersionResponse.model_validate(
        await save_draft(db, workspace_id, product_id, current_user.id, payload)
    )


@product_profile_router.post("/approve", response_model=ProductProfileVersionResponse)
async def approve_product_profile(
    workspace_id: UUID,
    product_id: UUID,
    payload: ProductProfileApproveRequest,
    current_user: MutationUserDep,
    db: DbDep,
) -> ProductProfileVersionResponse:
    return ProductProfileVersionResponse.model_validate(
        await approve_draft(
            db, workspace_id, product_id, current_user.id, payload.expected_revision
        )
    )


@product_profile_router.get("/versions", response_model=ProductProfileHistoryResponse)
async def get_product_profile_versions(
    workspace_id: UUID,
    product_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductProfileHistoryResponse:
    return ProductProfileHistoryResponse(
        versions=[
            ProductProfileVersionResponse.model_validate(row)
            for row in await profile_versions(
                db,
                workspace_id,
                product_id,
                current_user.id,
                limit=limit,
                offset=offset,
            )
        ]
    )


@product_profile_router.get("/versions/{version}", response_model=ProductProfileVersionResponse)
async def get_product_profile_version(
    workspace_id: UUID,
    product_id: UUID,
    version: int,
    current_user: CurrentUserDep,
    db: DbDep,
) -> ProductProfileVersionResponse:
    return ProductProfileVersionResponse.model_validate(
        await profile_version(db, workspace_id, product_id, current_user.id, version)
    )


@product_profile_router.get("/audit-events", response_model=list[ProductProfileAuditEventResponse])
async def get_product_profile_audit_events(
    workspace_id: UUID,
    product_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductProfileAuditEventResponse]:
    return [
        ProductProfileAuditEventResponse.model_validate(row)
        for row in await profile_audit_events(
            db,
            workspace_id,
            product_id,
            current_user.id,
            limit=limit,
            offset=offset,
        )
    ]
