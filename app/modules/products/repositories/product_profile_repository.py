from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.products.enums.product_profile_enum import ProductProfileStatus
from app.modules.products.models import ProductProfile, ProductProfileAuditEvent


async def get_product_profile_state(
    db: AsyncSession, workspace_id: UUID, product_id: UUID
) -> tuple[ProductProfile | None, ProductProfile | None]:
    rows = list(
        await db.scalars(
            select(ProductProfile).where(
                ProductProfile.workspace_id == workspace_id,
                ProductProfile.product_id == product_id,
                ProductProfile.status.in_(
                    [ProductProfileStatus.draft, ProductProfileStatus.approved]
                ),
            )
        )
    )
    return next((row for row in rows if row.status == ProductProfileStatus.draft), None), next(
        (row for row in rows if row.status == ProductProfileStatus.approved), None
    )


async def get_profile_for_update(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, status: ProductProfileStatus
) -> ProductProfile | None:
    return (
        await db.scalars(
            select(ProductProfile)
            .where(
                ProductProfile.workspace_id == workspace_id,
                ProductProfile.product_id == product_id,
                ProductProfile.status == status,
            )
            .with_for_update()
        )
    ).first()


async def get_profile_version(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, version: int
) -> ProductProfile | None:
    return (
        await db.scalars(
            select(ProductProfile).where(
                ProductProfile.workspace_id == workspace_id,
                ProductProfile.product_id == product_id,
                ProductProfile.version == version,
            )
        )
    ).first()


async def list_versions(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, *, limit: int, offset: int
) -> list[ProductProfile]:
    return list(
        (
            await db.scalars(
                select(ProductProfile)
                .where(
                    ProductProfile.workspace_id == workspace_id,
                    ProductProfile.product_id == product_id,
                    ProductProfile.version.is_not(None),
                )
                .order_by(ProductProfile.version.desc(), ProductProfile.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


async def list_audit_events(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, *, limit: int, offset: int
) -> list[ProductProfileAuditEvent]:
    return list(
        (
            await db.scalars(
                select(ProductProfileAuditEvent)
                .where(
                    ProductProfileAuditEvent.workspace_id == workspace_id,
                    ProductProfileAuditEvent.product_id == product_id,
                )
                .order_by(
                    ProductProfileAuditEvent.created_at.desc(),
                    ProductProfileAuditEvent.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
