from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.legacy.models import product_repositories, repos
from app.domains.products.models import Product
from app.domains.workspaces.models import WorkspaceMembership, WorkspaceRole


async def get_active_membership_role(
    db: AsyncSession, workspace_id: UUID, user_id: UUID
) -> WorkspaceRole | None:
    return cast(
        WorkspaceRole | None,
        await db.scalar(
            select(WorkspaceMembership.role).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.is_active.is_(True),
            )
        ),
    )


async def get_product_for_workspace(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID
) -> Product | None:
    statement = (
        select(Product)
        .join(
            WorkspaceMembership,
            WorkspaceMembership.workspace_id == Product.workspace_id,
        )
        .where(
            Product.id == product_id,
            Product.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.is_active.is_(True),
        )
    )
    return (await db.scalars(statement)).first()


async def get_product_workspace(
    db: AsyncSession, workspace_id: UUID, user_id: UUID
) -> tuple[list[Product], list[dict[str, object]]] | None:
    membership = await db.scalar(
        select(WorkspaceMembership.workspace_id).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.is_active.is_(True),
        )
    )
    if membership is None:
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
