from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.operator.models import OperatorRun
from app.modules.operator.services.opportunity_detection_service import persist_detection
from app.modules.operator.services.opportunity_snapshot_service import (
    MissingApprovedProfileError,
    MissingProductError,
    assemble_detection_input,
    persist_run_snapshot,
)


class OpportunityDetectionLeaseBusyError(RuntimeError):
    pass


class OpportunityDetectionLeaseLostError(RuntimeError):
    pass


class OpportunityDetectionTenantMismatchError(ValueError):
    pass


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class OpportunityDetectionWorkflow:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        now: Callable[[], datetime] | None = None,
        lease_duration: timedelta = timedelta(minutes=30),
    ) -> None:
        self.sessions = sessions
        self.now = now or (lambda: datetime.now(UTC))
        self.lease_duration = lease_duration

    async def _renew(self, run_id: UUID, lease_owner: str) -> None:
        async with self.sessions() as db:
            result = cast(
                CursorResult[object],
                await db.execute(
                    update(OperatorRun)
                    .where(
                        OperatorRun.id == run_id,
                        OperatorRun.status == "running",
                        OperatorRun.lease_owner == lease_owner,
                    )
                    .values(lease_expires_at=self.now() + self.lease_duration)
                ),
            )
            if result.rowcount != 1:
                await db.rollback()
                raise OpportunityDetectionLeaseLostError("Opportunity detection lease was lost.")
            await db.commit()

    async def process(
        self, run_id: UUID, workspace_id: UUID, product_id: UUID
    ) -> dict[str, object]:
        lease_owner = str(uuid4())
        async with self.sessions() as db:
            run = await db.scalar(
                select(OperatorRun).where(OperatorRun.id == run_id).with_for_update()
            )
            if run is None or run.run_kind != "opportunity_detection":
                return {"status": "not_found", "runId": str(run_id)}
            if run.workspace_id != workspace_id or run.product_id != product_id:
                raise OpportunityDetectionTenantMismatchError(
                    "Opportunity detection event tenant does not match the persisted run."
                )
            if run.status in {"completed", "stopped", "failed"}:
                if run.lease_owner is not None or run.lease_expires_at is not None:
                    run.lease_owner = None
                    run.lease_expires_at = None
                    await db.commit()
                return {"status": run.status, "runId": str(run.id), "summary": run.summary}
            claimed_at = self.now()
            if (
                run.status == "running"
                and run.lease_expires_at is not None
                and _as_utc(run.lease_expires_at) > _as_utc(claimed_at)
            ):
                raise OpportunityDetectionLeaseBusyError("Opportunity detection run is leased.")
            run.status = "running"
            run.lease_owner = lease_owner
            run.lease_expires_at = claimed_at + self.lease_duration
            if run.as_of is None:
                run.as_of = claimed_at
            run.failure_category = None
            await db.commit()
            as_of = _as_utc(run.as_of)
        assert product_id is not None and as_of is not None
        try:
            async with self.sessions() as db:
                assembly = await assemble_detection_input(db, workspace_id, product_id, as_of)
                await self._renew(run_id, lease_owner)
                await persist_run_snapshot(
                    db,
                    workspace_id=workspace_id,
                    product_id=product_id,
                    operator_run_id=run_id,
                    assembly=assembly,
                )
                evaluations = await persist_detection(
                    db,
                    workspace_id=workspace_id,
                    product_id=product_id,
                    operator_run_id=run_id,
                    assembly=assembly,
                    lease_owner=lease_owner,
                )
                current = await db.get(OperatorRun, run_id)
                assert current is not None
                return {
                    "status": current.status,
                    "runId": str(run_id),
                    "evaluations": len(evaluations),
                    "summary": current.summary,
                }
        except (MissingProductError, MissingApprovedProfileError) as exc:
            async with self.sessions() as db:
                result = cast(
                    CursorResult[object],
                    await db.execute(
                        update(OperatorRun)
                        .where(OperatorRun.id == run_id, OperatorRun.lease_owner == lease_owner)
                        .values(
                            status="stopped",
                            summary={
                                "reason": "missing_product"
                                if isinstance(exc, MissingProductError)
                                else "missing_approved_profile"
                            },
                            completed_at=self.now(),
                            lease_owner=None,
                            lease_expires_at=None,
                        )
                    ),
                )
                if result.rowcount != 1:
                    raise OpportunityDetectionLeaseLostError(
                        "Opportunity detection lease was lost."
                    ) from exc
                await db.commit()
                reason = (
                    "missing_product"
                    if isinstance(exc, MissingProductError)
                    else "missing_approved_profile"
                )
                return {"status": "stopped", "runId": str(run_id), "summary": {"reason": reason}}
        except Exception:
            async with self.sessions() as db:
                await db.execute(
                    update(OperatorRun)
                    .where(
                        OperatorRun.id == run_id,
                        OperatorRun.lease_owner == lease_owner,
                        OperatorRun.status != "completed",
                    )
                    .values(
                        status="pending",
                        failure_category="unexpected",
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                )
                await db.commit()
            raise
