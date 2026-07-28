from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.achievement_digests.models import achievement_digests
from app.modules.drafts.models import draft_feedback_events
from app.modules.llm.models import generation_runs
from app.modules.products.models import Product, product_repositories
from app.modules.repos.models import repos
from app.modules.workspaces.repositories import has_active_membership


async def get_product_for_workspace(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID
) -> Product | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None
    statement = select(Product).where(
        Product.id == product_id,
        Product.workspace_id == workspace_id,
    )
    return (await db.scalars(statement)).first()


async def get_product_workspace(
    db: AsyncSession, workspace_id: UUID, user_id: UUID
) -> tuple[list[Product], list[dict[str, object]]] | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None

    products = list(
        await db.scalars(
            select(Product)
            .where(Product.workspace_id == workspace_id)
            .order_by(Product.created_at.asc(), Product.id.asc())
        )
    )
    mappings = list(
        (
            await db.execute(
                select(product_repositories)
                .join(
                    Product,
                    (Product.id == product_repositories.c.product_id)
                    & (Product.workspace_id == product_repositories.c.workspace_id),
                )
                .join(
                    repos,
                    (repos.c.id == product_repositories.c.repo_id)
                    & (repos.c.workspace_id == product_repositories.c.workspace_id),
                )
                .where(product_repositories.c.workspace_id == workspace_id)
                .order_by(product_repositories.c.created_at.asc(), product_repositories.c.id.asc())
            )
        )
        .mappings()
        .all()
    )
    return products, [dict(mapping) for mapping in mappings]


async def get_product_for_update(
    db: AsyncSession, workspace_id: UUID, product_id: UUID
) -> Product | None:
    statement = (
        select(Product)
        .where(Product.workspace_id == workspace_id, Product.id == product_id)
        .with_for_update()
    )
    return (await db.scalars(statement)).first()


async def delete_product_records(db: AsyncSession, workspace_id: UUID, product: Product) -> None:
    await db.execute(
        delete(product_repositories).where(
            product_repositories.c.workspace_id == workspace_id,
            product_repositories.c.product_id == product.id,
        )
    )
    for historical_table in (generation_runs, achievement_digests, draft_feedback_events):
        await db.execute(
            update(historical_table)
            .where(
                historical_table.c.workspace_id == workspace_id,
                historical_table.c.product_id == product.id,
            )
            .values(product_id=None)
        )
    await db.delete(product)


async def lock_product_and_repository(
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


async def get_mapping_for_repo(
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


async def save_repository_assignment(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    repo_role: str,
    is_primary_repo: bool,
    existing: dict[str, Any] | None,
) -> None:
    if is_primary_repo:
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
                repo_role=repo_role,
                is_primary_repo=is_primary_repo,
            )
        )
        return
    moving = existing["product_id"] != product_id
    values: dict[str, object] = {
        "product_id": product_id,
        "repo_role": repo_role,
        "is_primary_repo": is_primary_repo,
    }
    if moving:
        values["role_description_override"] = None
    await db.execute(
        update(product_repositories)
        .where(product_repositories.c.id == existing["id"])
        .values(**values)
    )


async def delete_repository_assignment(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, repo_id: UUID
) -> int:
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
    return result.rowcount


async def set_repository_role_description(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    role_description_override: str | None,
) -> int:
    result = cast(
        CursorResult[Any],
        await db.execute(
            update(product_repositories)
            .where(
                product_repositories.c.workspace_id == workspace_id,
                product_repositories.c.product_id == product_id,
                product_repositories.c.repo_id == repo_id,
            )
            .values(role_description_override=role_description_override)
        ),
    )
    return result.rowcount
