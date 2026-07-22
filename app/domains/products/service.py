from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.legacy.models import (
    achievement_digests,
    draft_feedback_events,
    generation_runs,
    product_repositories,
    repos,
)
from app.domains.products.models import Product
from app.domains.products.repository import get_active_membership_role
from app.domains.products.schemas import (
    ProductRepositoryAssignment,
    ProductRepositoryRoleDescriptionUpdate,
    ProductWrite,
)
from app.domains.workspaces.models import WorkspaceRole
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError

WRITE_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor}


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
    if role not in WRITE_ROLES:
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
    product = await db.scalar(
        select(Product)
        .where(Product.workspace_id == workspace_id, Product.id == product_id)
        .with_for_update()
    )
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
    product = await db.scalar(
        select(Product)
        .where(Product.workspace_id == workspace_id, Product.id == product_id)
        .with_for_update()
    )
    if product is None:
        raise NotFoundError("Product not found.", code="product_not_found")
    await db.execute(
        delete(product_repositories).where(
            product_repositories.c.workspace_id == workspace_id,
            product_repositories.c.product_id == product_id,
        )
    )
    for historical_table in (generation_runs, achievement_digests, draft_feedback_events):
        await db.execute(
            update(historical_table)
            .where(
                historical_table.c.workspace_id == workspace_id,
                historical_table.c.product_id == product_id,
            )
            .values(product_id=None)
        )
    await db.delete(product)
    await _commit(db)


async def _lock_product_and_repository(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, repo_id: UUID
) -> bool | None:
    product = await db.scalar(
        select(Product.id)
        .where(Product.workspace_id == workspace_id, Product.id == product_id)
        .with_for_update()
    )
    if product is None:
        return None
    return await db.scalar(
        select(repos.c.is_tracked)
        .where(repos.c.workspace_id == workspace_id, repos.c.id == repo_id)
        .with_for_update()
    )


async def _mapping_for_repo(
    db: AsyncSession, workspace_id: UUID, repo_id: UUID
) -> dict[str, Any] | None:
    mapping = (
        (
            await db.execute(
                select(product_repositories)
                .where(
                    product_repositories.c.workspace_id == workspace_id,
                    product_repositories.c.repo_id == repo_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    return dict(mapping) if mapping is not None else None


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
    tracked = await _lock_product_and_repository(db, workspace_id, product_id, repo_id)
    if tracked is None:
        raise NotFoundError("Product repository not found.", code="product_repository_not_found")
    if not tracked:
        raise ConflictError(
            "Only tracked repositories can be assigned to products.",
            code="repository_not_tracked",
        )
    existing = await _mapping_for_repo(db, workspace_id, repo_id)
    if payload.is_primary_repo:
        await db.execute(
            update(product_repositories)
            .where(
                product_repositories.c.workspace_id == workspace_id,
                product_repositories.c.product_id == product_id,
                product_repositories.c.is_primary_repo.is_(True),
            )
            .values(is_primary_repo=False)
        )
    if existing is None:
        await db.execute(
            insert(product_repositories).values(
                id=uuid4(),
                workspace_id=workspace_id,
                product_id=product_id,
                repo_id=repo_id,
                repo_role=payload.repo_role,
                is_primary_repo=payload.is_primary_repo,
            )
        )
    else:
        moving = existing["product_id"] != product_id
        values: dict[str, object] = {
            "product_id": product_id,
            "repo_role": payload.repo_role,
            "is_primary_repo": payload.is_primary_repo,
        }
        if moving:
            values["role_description_override"] = None
        await db.execute(
            update(product_repositories)
            .where(product_repositories.c.id == existing["id"])
            .values(**values)
        )
    try:
        await _commit(db)
    except IntegrityError as error:
        raise ConflictError(
            "Repository assignment conflicts with an existing assignment.",
            code="repository_assignment_conflict",
        ) from error
    mapping = await _mapping_for_repo(db, workspace_id, repo_id)
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
    result = cast(
        CursorResult[Any],
        await db.execute(
            delete(product_repositories).where(
                product_repositories.c.workspace_id == workspace_id,
                product_repositories.c.product_id == product_id,
                product_repositories.c.repo_id == repo_id,
            )
        ),
    )
    if result.rowcount != 1:
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
    result = cast(
        CursorResult[Any],
        await db.execute(
            update(product_repositories)
            .where(
                product_repositories.c.workspace_id == workspace_id,
                product_repositories.c.product_id == product_id,
                product_repositories.c.repo_id == repo_id,
            )
            .values(role_description_override=payload.role_description_override)
        ),
    )
    if result.rowcount != 1:
        await db.rollback()
        raise NotFoundError("Product repository not found.", code="product_repository_not_found")
    await _commit(db)
    mapping = await _mapping_for_repo(db, workspace_id, repo_id)
    assert mapping is not None
    return dict(mapping)
