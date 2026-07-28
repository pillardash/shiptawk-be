from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import OperatorRun


async def get_product_run(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    run_id: UUID,
    *,
    for_update: bool = False,
) -> OperatorRun | None:
    statement = select(OperatorRun).where(
        OperatorRun.workspace_id == workspace_id,
        OperatorRun.product_id == product_id,
        OperatorRun.id == run_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return cast(
        OperatorRun | None,
        await db.scalar(statement),
    )


async def flush(db: AsyncSession, run: OperatorRun) -> None:
    db.add(run)
    await db.flush()
