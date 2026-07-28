from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.products.models import product_repositories
from app.modules.repos.models import repos
from app.modules.repos.schemas.product_repository_evidence_schema import ProductRepositoryEvidence


async def list_product_repository_evidence(
    db: AsyncSession, workspace_id: UUID, product_id: UUID
) -> list[ProductRepositoryEvidence]:
    rows = (
        await db.execute(
            select(
                repos.c.repo_name,
                repos.c.repo_full_name,
                repos.c.description,
                product_repositories.c.repo_role,
                product_repositories.c.role_description_override,
                product_repositories.c.generated_role_description,
            )
            .select_from(
                product_repositories.join(
                    repos,
                    (repos.c.id == product_repositories.c.repo_id)
                    & (repos.c.workspace_id == product_repositories.c.workspace_id),
                )
            )
            .where(
                product_repositories.c.workspace_id == workspace_id,
                product_repositories.c.product_id == product_id,
                repos.c.is_tracked.is_(True),
            )
            .order_by(product_repositories.c.is_primary_repo.desc(), repos.c.id.asc())
        )
    ).mappings()
    return [
        ProductRepositoryEvidence(
            name=row["repo_name"],
            full_name=row["repo_full_name"],
            description=row["description"],
            role=row["repo_role"],
            role_description=row["role_description_override"] or row["generated_role_description"],
        )
        for row in rows
    ]
