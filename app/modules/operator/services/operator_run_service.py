import hashlib
import json
from datetime import datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.idempotency import insert_or_replay
from app.db.outbox import OutboxMetadata, enqueue_outbox_event
from app.modules.operator.models import OperatorRun
from app.modules.operator.policies import WRITE_ROLES, require_role
from app.modules.products.models import Product


class OperatorRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    trigger: Literal["manual"] = "manual"


class OperatorRunIdempotencyConflictError(ValueError):
    pass


class ActiveOperatorRunConflictError(ValueError):
    pass


class ProductNotFoundError(LookupError):
    pass


def _fingerprint(request: OperatorRunRequest) -> str:
    body = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


async def request_operator_run(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    idempotency_key: str,
    request: OperatorRunRequest,
    now: datetime | None = None,
) -> OperatorRun:
    key = idempotency_key.strip()
    if not key or len(key) > 255:
        raise ValueError("Idempotency-Key must contain between 1 and 255 characters.")
    await require_role(db, workspace_id, actor_id, WRITE_ROLES)
    product = await db.scalar(
        select(Product)
        .where(Product.workspace_id == workspace_id, Product.id == product_id)
        .with_for_update()
    )
    if product is None:
        raise ProductNotFoundError("Product was not found.")
    fingerprint = _fingerprint(request)
    replay = await db.scalar(
        select(OperatorRun).where(
            OperatorRun.workspace_id == workspace_id,
            OperatorRun.product_id == product_id,
            OperatorRun.idempotency_key == key,
        )
    )
    if replay is None:
        active = await db.scalar(
            select(OperatorRun.id).where(
                OperatorRun.workspace_id == workspace_id,
                OperatorRun.product_id == product_id,
                OperatorRun.run_kind == "opportunity_detection",
                OperatorRun.status.in_(("pending", "running")),
            )
        )
        if active is not None:
            raise ActiveOperatorRunConflictError(
                "Another opportunity detection run is already active for this product."
            )
    run_id = uuid4()
    del now
    try:
        result = await insert_or_replay(
            db,
            cast(Table, OperatorRun.__table__),
            values={
                "id": run_id,
                "workspace_id": workspace_id,
                "product_id": product_id,
                "run_kind": "opportunity_detection",
                "trigger": request.trigger,
                "status": "pending",
                "idempotency_key": key,
                "request_fingerprint": fingerprint,
                "as_of": None,
                "input_schema_version": "1",
                "detector_suite_version": "v1",
                "quality_policy_version": "v1",
                "scoring_policy_version": "v1",
                "cooldown_policy_version": "v1",
                "analyzer_version": "opportunity-detection-v1",
                "provenance": {},
                "summary": {},
                "created_by_actor_id": actor_id,
            },
            conflict_columns=("workspace_id", "product_id", "idempotency_key"),
        )
        if result.value["request_fingerprint"] != fingerprint:
            raise OperatorRunIdempotencyConflictError(
                "Idempotency key was already used with another body."
            )
        stable_run_id = cast(UUID, result.value["id"])
        await enqueue_outbox_event(
            db,
            event_name="operator/opportunities.detect.requested",
            schema_version=1,
            idempotency_key=str(stable_run_id),
            aggregate_id=stable_run_id,
            workspace_id=workspace_id,
            product_id=product_id,
            metadata=OutboxMetadata(
                {
                    "runId": str(stable_run_id),
                    "workspaceId": str(workspace_id),
                    "productId": str(product_id),
                    "schemaVersion": 1,
                    "correlationId": str(stable_run_id),
                }
            ),
        )
        await db.commit()
        run = await db.get(OperatorRun, stable_run_id, populate_existing=True)
        if run is None:
            raise RuntimeError("Operator run was inserted but could not be loaded.")
        return run
    except Exception:
        await db.rollback()
        raise
