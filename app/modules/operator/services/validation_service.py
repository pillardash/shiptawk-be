from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.evidence.models import EvidenceItem
from app.modules.operator.policies import (
    APPROVED_EVIDENCE_STATUS,
    BLOCKED_EVIDENCE_CLASSIFICATIONS,
)
from app.modules.products.models import Product
from app.shared.exceptions import ConflictError, NotFoundError


async def validate_product(db: AsyncSession, workspace_id: UUID, product_id: UUID | None) -> None:
    if product_id is None:
        return
    product = await db.scalar(
        select(Product.id).where(Product.workspace_id == workspace_id, Product.id == product_id)
    )
    if product is None:
        raise NotFoundError("Product not found.", code="product_not_found")


async def approved_evidence(
    db: AsyncSession, workspace_id: UUID, product_id: UUID | None
) -> list[EvidenceItem]:
    query = select(EvidenceItem).where(
        EvidenceItem.workspace_id == workspace_id,
        EvidenceItem.status == APPROVED_EVIDENCE_STATUS,
        EvidenceItem.classification.notin_(BLOCKED_EVIDENCE_CLASSIFICATIONS),
    )
    if product_id is not None:
        query = query.where(EvidenceItem.product_id == product_id)
    return list(await db.scalars(query.order_by(EvidenceItem.observed_at.desc(), EvidenceItem.id)))


async def validate_evidence_ids(
    db: AsyncSession, workspace_id: UUID, evidence_ids: list[UUID]
) -> None:
    found = set(
        await db.scalars(
            select(EvidenceItem.id).where(
                EvidenceItem.workspace_id == workspace_id,
                EvidenceItem.id.in_(evidence_ids),
                EvidenceItem.status == APPROVED_EVIDENCE_STATUS,
                EvidenceItem.classification.notin_(BLOCKED_EVIDENCE_CLASSIFICATIONS),
            )
        )
    )
    if found != set(evidence_ids):
        raise ConflictError(
            "Recommendations may reference only approved workspace evidence.",
            code="recommendation_evidence_invalid",
        )
