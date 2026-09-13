from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.services.product_event_service import write_product_event
from app.modules.products.models import Product
from app.modules.products.repositories.product_repository import (
    delete_product_records,
    delete_repository_assignment,
    get_mapping_for_repo,
    get_product_for_update,
    lock_product_and_repository,
    save_repository_assignment,
    set_repository_role_description,
)
from app.modules.products.schemas.product_schema import (
    ProductRepositoryAssignment,
    ProductRepositoryRoleDescriptionUpdate,
    ProductWrite,
)
from app.modules.workspaces.models import Workspace
from app.modules.workspaces.policies import can_write_products
from app.modules.workspaces.repositories import get_active_membership_role
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError


async def _require_write_access(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    *,
    missing_code: str,
    missing_message: str,
) -> None:
    role = await get_active_membership_role(db, workspace_id, user_id)
    if role is None:
        raise NotFoundError(missing_message, code=missing_code)
    if not can_write_products(role):
        raise ForbiddenError(
            "Product write access requires an owner, admin, or editor role.",
            code="product_write_forbidden",
        )


def _optional(value: str) -> str | None:
    return value or None


def _product_values(payload: ProductWrite) -> dict[str, object]:
    return {
        "name": payload.name,
        "description": _optional(payload.description),
        "website_url": _optional(payload.website_url),
        "target_audience": _optional(payload.target_audience),
        "messaging_angle": _optional(payload.messaging_angle),
        "tone_override": payload.tone_override,
        "blocked_terms": payload.blocked_terms,
        "blocked_topics": payload.blocked_topics,
        "safe_public_boundaries": payload.safe_public_boundaries,
        "primary_customer_pain": _optional(payload.primary_customer_pain),
        "desired_outcome": _optional(payload.desired_outcome),
        "positioning_statement": _optional(payload.positioning_statement),
        "proof_points": payload.proof_points,
        "customer_use_cases": payload.customer_use_cases,
        "content_goal": payload.content_goal,
        "cta_preference": _optional(payload.cta_preference),
        "founder_story_angle": _optional(payload.founder_story_angle),
    }


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def create_product(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, payload: ProductWrite
) -> Product:
    await _require_write_access(
        db,
        workspace_id,
        user_id,
        missing_code="product_workspace_not_found",
        missing_message="Product workspace not found.",
    )
    product = Product(workspace_id=workspace_id, user_id=user_id, **_product_values(payload))
    db.add(product)
    await db.flush()
    workspace = await db.scalar(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
    )
    if workspace is not None and workspace.activation_product_id is None:
        workspace.activation_product_id = product.id
    await write_product_event(
        db,
        workspace_id=workspace_id,
        product_id=product.id,
        actor_id=user_id,
        event_name="product_created",
        idempotency_key=f"product-created:{product.id}",
        resource_type="product",
        resource_id=product.id,
    )
    await _commit(db)
    await db.refresh(product)
    return product


async def update_product(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    payload: ProductWrite,
) -> Product:
    await _require_write_access(
        db,
        workspace_id,
        user_id,
        missing_code="product_not_found",
        missing_message="Product not found.",
    )
    product = await get_product_for_update(db, workspace_id, product_id)
    if product is None:
        raise NotFoundError("Product not found.", code="product_not_found")
    for field, value in _product_values(payload).items():
        setattr(product, field, value)
    product.updated_at = datetime.now(UTC)
    await _commit(db)
    await db.refresh(product)
    return product


async def delete_product(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID
) -> None:
    await _require_write_access(
        db,
        workspace_id,
        user_id,
        missing_code="product_not_found",
        missing_message="Product not found.",
    )
    product = await get_product_for_update(db, workspace_id, product_id)
    if product is None:
        raise NotFoundError("Product not found.", code="product_not_found")
    await delete_product_records(db, workspace_id, product)
    await _commit(db)


async def assign_repository(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    user_id: UUID,
    payload: ProductRepositoryAssignment,
) -> dict[str, object]:
    await _require_write_access(
        db,
        workspace_id,
        user_id,
        missing_code="product_repository_not_found",
        missing_message="Product repository not found.",
    )
    tracked = await lock_product_and_repository(db, workspace_id, product_id, repo_id)
    if tracked is None:
        raise NotFoundError("Product repository not found.", code="product_repository_not_found")
    if not tracked:
        raise ConflictError(
            "Only tracked repositories can be assigned to products.",
            code="repository_not_tracked",
        )
    existing = await get_mapping_for_repo(db, workspace_id, repo_id)
    await save_repository_assignment(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        repo_id=repo_id,
        repo_role=payload.repo_role,
        is_primary_repo=payload.is_primary_repo,
        existing=existing,
    )
    try:
        await _commit(db)
    except IntegrityError as error:
        raise ConflictError(
            "Repository assignment conflicts with an existing assignment.",
            code="repository_assignment_conflict",
        ) from error
    mapping = await get_mapping_for_repo(db, workspace_id, repo_id)
    assert mapping is not None
    return dict(mapping)


async def remove_repository(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    user_id: UUID,
) -> None:
    await _require_write_access(
        db,
        workspace_id,
        user_id,
        missing_code="product_repository_not_found",
        missing_message="Product repository not found.",
    )
    deleted = await delete_repository_assignment(db, workspace_id, product_id, repo_id)
    if deleted != 1:
        await db.rollback()
        raise NotFoundError("Product repository not found.", code="product_repository_not_found")
    await _commit(db)


async def update_repository_role_description(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    user_id: UUID,
    payload: ProductRepositoryRoleDescriptionUpdate,
) -> dict[str, object]:
    await _require_write_access(
        db,
        workspace_id,
        user_id,
        missing_code="product_repository_not_found",
        missing_message="Product repository not found.",
    )
    updated = await set_repository_role_description(
        db,
        workspace_id,
        product_id,
        repo_id,
        payload.role_description_override,
    )
    if updated != 1:
        await db.rollback()
        raise NotFoundError("Product repository not found.", code="product_repository_not_found")
    await _commit(db)
    mapping = await get_mapping_for_repo(db, workspace_id, repo_id)
    assert mapping is not None
    return dict(mapping)
