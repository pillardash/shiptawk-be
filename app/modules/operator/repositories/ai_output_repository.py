from typing import cast
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import (
    ActionRiskReview,
    OperatorRecommendation,
    OpportunityEvidence,
    PreparedAsset,
    operator_recommendation_evidence,
)


async def list_public_fresh_evidence(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, opportunity_id: UUID
) -> list[OpportunityEvidence]:
    return list(
        await db.scalars(
            select(OpportunityEvidence).where(
                OpportunityEvidence.workspace_id == workspace_id,
                OpportunityEvidence.product_id == product_id,
                OpportunityEvidence.opportunity_id == opportunity_id,
                OpportunityEvidence.freshness_status == "fresh",
                OpportunityEvidence.public_safe_summary != "",
            )
        )
    )


async def list_recommendation_evidence(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, recommendation_id: UUID
) -> list[OpportunityEvidence]:
    return list(
        await db.scalars(
            select(OpportunityEvidence)
            .join(
                operator_recommendation_evidence,
                (
                    operator_recommendation_evidence.c.workspace_id
                    == OpportunityEvidence.workspace_id
                )
                & (operator_recommendation_evidence.c.product_id == OpportunityEvidence.product_id)
                & (operator_recommendation_evidence.c.evidence_id == OpportunityEvidence.id),
            )
            .where(
                operator_recommendation_evidence.c.workspace_id == workspace_id,
                operator_recommendation_evidence.c.product_id == product_id,
                operator_recommendation_evidence.c.recommendation_id == recommendation_id,
                OpportunityEvidence.freshness_status == "fresh",
                OpportunityEvidence.public_safe_summary != "",
            )
        )
    )


async def get_recommendation_by_key(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    opportunity_id: UUID,
    idempotency_key: str,
) -> OperatorRecommendation | None:
    return cast(
        OperatorRecommendation | None,
        await db.scalar(
            select(OperatorRecommendation).where(
                OperatorRecommendation.workspace_id == workspace_id,
                OperatorRecommendation.product_id == product_id,
                OperatorRecommendation.opportunity_id == opportunity_id,
                OperatorRecommendation.idempotency_key == idempotency_key,
            )
        ),
    )


async def add_recommendation(
    db: AsyncSession, recommendation: OperatorRecommendation, evidence_ids: list[UUID]
) -> OperatorRecommendation:
    db.add(recommendation)
    await db.flush()
    if evidence_ids:
        await db.execute(
            insert(operator_recommendation_evidence),
            [
                {
                    "workspace_id": recommendation.workspace_id,
                    "product_id": recommendation.product_id,
                    "recommendation_id": recommendation.id,
                    "evidence_id": evidence_id,
                }
                for evidence_id in evidence_ids
            ],
        )
    return recommendation


async def add_asset(db: AsyncSession, asset: PreparedAsset) -> PreparedAsset:
    db.add(asset)
    await db.flush()
    return asset


async def get_asset_by_revision(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    recommendation_id: UUID,
    revision: int,
) -> PreparedAsset | None:
    return cast(
        PreparedAsset | None,
        await db.scalar(
            select(PreparedAsset).where(
                PreparedAsset.workspace_id == workspace_id,
                PreparedAsset.product_id == product_id,
                PreparedAsset.recommendation_id == recommendation_id,
                PreparedAsset.revision == revision,
            )
        ),
    )


async def add_risk_review(db: AsyncSession, review: ActionRiskReview) -> ActionRiskReview:
    db.add(review)
    await db.flush()
    return review


async def get_risk_review(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    asset_id: UUID,
    asset_revision: int,
) -> ActionRiskReview | None:
    return cast(
        ActionRiskReview | None,
        await db.scalar(
            select(ActionRiskReview).where(
                ActionRiskReview.workspace_id == workspace_id,
                ActionRiskReview.product_id == product_id,
                ActionRiskReview.asset_id == asset_id,
                ActionRiskReview.asset_revision == asset_revision,
            )
        ),
    )
