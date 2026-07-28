from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.llm.models.llm_execution import LLMExecution
from app.modules.llm.models.llm_execution_attempt import LLMExecutionAttempt


def _key_query(
    workspace_id: UUID, product_id: UUID, use_case: str, idempotency_key: str
) -> Select[tuple[LLMExecution]]:
    return select(LLMExecution).where(
        LLMExecution.workspace_id == workspace_id,
        LLMExecution.product_id == product_id,
        LLMExecution.use_case == use_case,
        LLMExecution.idempotency_key == idempotency_key,
    )


async def get_by_key(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    use_case: str,
    idempotency_key: str,
    *,
    for_update: bool = False,
) -> LLMExecution | None:
    statement = _key_query(workspace_id, product_id, use_case, idempotency_key)
    if for_update:
        statement = statement.with_for_update()
    return cast(LLMExecution | None, await db.scalar(statement))


async def flush_execution(db: AsyncSession, execution: LLMExecution) -> None:
    db.add(execution)
    await db.flush()


async def acquire_lease(
    db: AsyncSession,
    execution_id: UUID,
    workspace_id: UUID,
    product_id: UUID,
    *,
    owner: str,
    token: UUID,
    now: datetime,
    expires_at: datetime,
) -> bool:
    statement = (
        update(LLMExecution)
        .where(
            LLMExecution.id == execution_id,
            LLMExecution.workspace_id == workspace_id,
            LLMExecution.product_id == product_id,
            LLMExecution.status.in_(("pending", "running")),
            (LLMExecution.lease_expires_at.is_(None) | (LLMExecution.lease_expires_at <= now)),
        )
        .values(status="running", lease_owner=owner, lease_token=token, lease_expires_at=expires_at)
    )
    result = await db.execute(statement)
    return cast(int, getattr(result, "rowcount", 0)) == 1


async def reopen_transient_failure(
    db: AsyncSession,
    execution_id: UUID,
    workspace_id: UUID,
    product_id: UUID,
) -> bool:
    result = await db.execute(
        update(LLMExecution)
        .where(
            LLMExecution.id == execution_id,
            LLMExecution.workspace_id == workspace_id,
            LLMExecution.product_id == product_id,
            LLMExecution.status == "failed",
            LLMExecution.failure_category == "LLMTransientError",
        )
        .values(status="pending", failure_category=None)
    )
    return cast(int, getattr(result, "rowcount", 0)) == 1


async def allocate_attempt(
    db: AsyncSession, execution_id: UUID, workspace_id: UUID, product_id: UUID, lease_token: UUID
) -> int | None:
    statement = (
        update(LLMExecution)
        .where(
            LLMExecution.id == execution_id,
            LLMExecution.workspace_id == workspace_id,
            LLMExecution.product_id == product_id,
            LLMExecution.lease_token == lease_token,
            LLMExecution.lease_expires_at > func.now(),
        )
        .values(attempt_count=LLMExecution.attempt_count + 1)
        .returning(LLMExecution.attempt_count)
    )
    return cast(int | None, await db.scalar(statement))


async def renew_lease(
    db: AsyncSession,
    execution_id: UUID,
    workspace_id: UUID,
    product_id: UUID,
    lease_token: UUID,
    *,
    expires_at: datetime,
) -> bool:
    result = await db.execute(
        update(LLMExecution)
        .where(
            LLMExecution.id == execution_id,
            LLMExecution.workspace_id == workspace_id,
            LLMExecution.product_id == product_id,
            LLMExecution.status == "running",
            LLMExecution.lease_token == lease_token,
            LLMExecution.lease_expires_at > func.now(),
        )
        .values(lease_expires_at=expires_at)
    )
    return cast(int, getattr(result, "rowcount", 0)) == 1


async def flush_attempt(db: AsyncSession, attempt: LLMExecutionAttempt) -> None:
    db.add(attempt)
    await db.flush()


async def fence_update(
    db: AsyncSession,
    execution_id: UUID,
    workspace_id: UUID,
    product_id: UUID,
    expected_lease_token: UUID,
    **values: object,
) -> bool:
    statement = (
        update(LLMExecution)
        .where(
            LLMExecution.id == execution_id,
            LLMExecution.workspace_id == workspace_id,
            LLMExecution.product_id == product_id,
            LLMExecution.lease_token == expected_lease_token,
            LLMExecution.lease_expires_at > func.now(),
        )
        .values(**values)
    )
    result = await db.execute(statement)
    return cast(int, getattr(result, "rowcount", 0)) == 1
