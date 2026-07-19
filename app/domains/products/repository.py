from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.products.models import Product
from app.domains.workspaces.models import WorkspaceMembership


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
