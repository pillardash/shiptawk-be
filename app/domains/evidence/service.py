from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.models import EvidenceItem, EvidenceReview
from app.domains.evidence.repository import get_latest_review
from app.domains.evidence.schemas import EvidenceCreate
from app.domains.products.models import Product
from app.domains.products.repository import get_active_membership_role
from app.domains.workspaces.models import WorkspaceRole
from app.shared.exceptions import ConflictError, ForbiddenError, NotFoundError

WRITE_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.editor}
REVIEW_ROLES = {WorkspaceRole.owner, WorkspaceRole.admin, WorkspaceRole.reviewer}


async def create_evidence(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, payload: EvidenceCreate
) -> EvidenceItem:
    role = await get_active_membership_role(db, workspace_id, user_id)
    if role is None:
        raise NotFoundError("Evidence workspace not found.", code="evidence_workspace_not_found")
    if role not in WRITE_ROLES:
        raise ForbiddenError(
            "Evidence write access requires an owner, admin, or editor role.",
            code="evidence_write_forbidden",
        )
    if payload.product_id is not None:
        product = await db.scalar(
            select(Product.id).where(
                Product.workspace_id == workspace_id, Product.id == payload.product_id
            )
        )
        if product is None:
            raise NotFoundError("Evidence product not found.", code="evidence_product_not_found")
    item = EvidenceItem(
        workspace_id=workspace_id,
        product_id=payload.product_id,
        evidence_type=payload.evidence_type,
        title=payload.title,
        content=payload.content,
        public_safe_summary=payload.public_safe_summary,
        what_happened=payload.what_happened,
        why_it_matters=payload.why_it_matters,
        source_type=payload.evidence_type,
        source_reference=payload.source_reference,
        source_url=payload.source_url,
        observed_at=payload.observed_at,
        confidence=payload.confidence,
        classification=payload.classification,
        expires_at=payload.expires_at,
        created_by_actor_id=user_id,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def review_evidence(
    db: AsyncSession,
    workspace_id: UUID,
    evidence_id: UUID,
    user_id: UUID,
    decision: str,
    reason: str | None,
) -> tuple[EvidenceItem, EvidenceReview]:
    role = await get_active_membership_role(db, workspace_id, user_id)
    if role is None:
        raise NotFoundError("Evidence not found.", code="evidence_not_found")
    if role not in REVIEW_ROLES:
        raise ForbiddenError(
            "Evidence review requires an owner, admin, or reviewer role.",
            code="evidence_review_forbidden",
        )
    item = await db.scalar(
        select(EvidenceItem)
        .where(EvidenceItem.workspace_id == workspace_id, EvidenceItem.id == evidence_id)
        .with_for_update()
    )
    if item is None:
        raise NotFoundError("Evidence not found.", code="evidence_not_found")
    if item.status != "pending" or await get_latest_review(db, workspace_id, evidence_id):
        raise ConflictError(
            "Evidence has already received a review decision.", code="evidence_already_reviewed"
        )
    now = datetime.now(UTC)
    item.status = decision
    item.updated_at = now
    if decision == "approved":
        item.last_verified_at = now
    review = EvidenceReview(
        workspace_id=workspace_id,
        evidence_item_id=evidence_id,
        decision=decision,
        reason=reason,
        reviewer_id=user_id,
        reviewed_at=now,
    )
    db.add(review)
    await db.commit()
    await db.refresh(item)
    await db.refresh(review)
    return item, review
