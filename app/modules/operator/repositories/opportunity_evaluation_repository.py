from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import OpportunityEvaluation, OpportunityEvidence


async def list_for_run(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, run_id: UUID
) -> list[OpportunityEvaluation]:
    return list(
        await db.scalars(
            select(OpportunityEvaluation)
            .where(
                OpportunityEvaluation.workspace_id == workspace_id,
                OpportunityEvaluation.product_id == product_id,
                OpportunityEvaluation.operator_run_id == run_id,
            )
            .order_by(OpportunityEvaluation.evaluation_key)
        )
    )


async def add(db: AsyncSession, evaluation: OpportunityEvaluation) -> OpportunityEvaluation:
    db.add(evaluation)
    await db.flush()
    return evaluation


async def add_evidence(db: AsyncSession, evidence: OpportunityEvidence) -> OpportunityEvidence:
    db.add(evidence)
    await db.flush()
    return evidence
