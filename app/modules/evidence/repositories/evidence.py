from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.evidence.models import (
    Claim,
    ClaimEvidenceLink,
    EvidenceExcerpt,
    EvidenceItem,
    EvidenceReview,
)
from app.modules.workspaces.repositories import has_active_membership


async def list_evidence(
    db: AsyncSession, workspace_id: UUID, user_id: UUID
) -> list[EvidenceItem] | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None
    return list(
        await db.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.workspace_id == workspace_id)
            .order_by(EvidenceItem.created_at.desc(), EvidenceItem.id.asc())
        )
    )


async def get_evidence(
    db: AsyncSession, workspace_id: UUID, evidence_id: UUID, user_id: UUID
) -> EvidenceItem | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None
    return cast(
        EvidenceItem | None,
        await db.scalar(
            select(EvidenceItem).where(
                EvidenceItem.workspace_id == workspace_id, EvidenceItem.id == evidence_id
            )
        ),
    )


async def get_latest_review(
    db: AsyncSession, workspace_id: UUID, evidence_id: UUID
) -> EvidenceReview | None:
    return cast(
        EvidenceReview | None,
        await db.scalar(
            select(EvidenceReview)
            .where(
                EvidenceReview.workspace_id == workspace_id,
                EvidenceReview.evidence_item_id == evidence_id,
            )
            .order_by(EvidenceReview.reviewed_at.desc(), EvidenceReview.id.desc())
            .limit(1)
        ),
    )


async def list_claim_rows(
    db: AsyncSession, workspace_id: UUID, user_id: UUID
) -> list[tuple[Claim, list[dict[str, object]]]] | None:
    if not await has_active_membership(db, workspace_id, user_id):
        return None
    claims = list(
        await db.scalars(
            select(Claim)
            .where(Claim.workspace_id == workspace_id)
            .order_by(Claim.created_at.asc(), Claim.id.asc())
        )
    )
    result: list[tuple[Claim, list[dict[str, object]]]] = []
    for claim in claims:
        rows = (
            (
                await db.execute(
                    select(
                        EvidenceItem.id.label("evidence_id"),
                        EvidenceExcerpt.id.label("evidence_excerpt_id"),
                        EvidenceItem.title.label("evidence_title"),
                        EvidenceItem.status.label("evidence_status"),
                        EvidenceExcerpt.text.label("excerpt"),
                        EvidenceExcerpt.locator,
                    )
                    .join(
                        ClaimEvidenceLink,
                        (ClaimEvidenceLink.workspace_id == EvidenceExcerpt.workspace_id)
                        & (ClaimEvidenceLink.evidence_excerpt_id == EvidenceExcerpt.id),
                    )
                    .join(
                        EvidenceItem,
                        (EvidenceItem.workspace_id == EvidenceExcerpt.workspace_id)
                        & (EvidenceItem.id == EvidenceExcerpt.evidence_item_id),
                    )
                    .where(
                        ClaimEvidenceLink.workspace_id == workspace_id,
                        ClaimEvidenceLink.claim_id == claim.id,
                    )
                    .order_by(ClaimEvidenceLink.created_at.asc(), ClaimEvidenceLink.id.asc())
                )
            )
            .mappings()
            .all()
        )
        result.append((claim, [dict(row) for row in rows]))
    return result
