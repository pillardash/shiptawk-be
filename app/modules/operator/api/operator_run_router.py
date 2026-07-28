from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import func, select

from app.api.deps import DbDep, MutationUserDep, get_current_user
from app.modules.identity.models.users import User
from app.modules.operator.models import OperatorRun, OpportunityEvaluation
from app.modules.operator.schemas.operator_api_schema import (
    OperatorRunListSchema,
    OperatorRunRequestSchema,
    OperatorRunResponseSchema,
    OpportunityEvaluationResponseSchema,
)
from app.modules.operator.services.operator_run_service import (
    ActiveOperatorRunConflictError,
    OperatorRunIdempotencyConflictError,
    OperatorRunRequest,
    ProductNotFoundError,
    request_operator_run,
)
from app.modules.operator.services.weekly_growth_service import request_manual_weekly_run
from app.modules.products.models import Product
from app.modules.workspaces.services import require_active_workspace_role
from app.shared.exceptions import BadRequestError, ConflictError, NotFoundError

router = APIRouter(
    prefix="/workspaces/{workspace_id}/products/{product_id}/operator/runs", tags=["operator-runs"]
)
CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def _read_access(db: DbDep, workspace_id: UUID, user_id: UUID) -> None:
    await require_active_workspace_role(
        db,
        workspace_id,
        user_id,
        missing=NotFoundError("Operator run not found.", code="operator_run_not_found"),
    )


@router.post("", response_model=OperatorRunResponseSchema, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    workspace_id: UUID,
    product_id: UUID,
    payload: OperatorRunRequestSchema,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> OperatorRunResponseSchema:
    if payload.run_kind == "weekly_growth":
        product = await db.scalar(
            select(Product).where(Product.workspace_id == workspace_id, Product.id == product_id)
        )
        if product is None:
            raise NotFoundError("Product not found.", code="product_not_found")
        if not product.operator_manual_enabled:
            raise ConflictError("Manual operator runs are disabled.", code="manual_runs_disabled")
        now = datetime.now(UTC)
        period_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        run = await request_manual_weekly_run(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            actor_id=user.id,
            period_start=period_start,
            period_end=period_start + timedelta(days=7),
            workflow_version="weekly-growth-v1",
            idempotency_key=idempotency_key,
        )
        return OperatorRunResponseSchema.model_validate(run)
    try:
        run = await request_operator_run(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            actor_id=user.id,
            idempotency_key=idempotency_key,
            request=OperatorRunRequest.model_validate(payload.model_dump(exclude={"run_kind"})),
        )
    except (OperatorRunIdempotencyConflictError, ActiveOperatorRunConflictError) as exc:
        raise ConflictError(str(exc), code="operator_run_idempotency_conflict") from exc
    except ProductNotFoundError as exc:
        raise NotFoundError(str(exc), code="product_not_found") from exc
    except ValueError as exc:
        raise BadRequestError(str(exc), code="invalid_idempotency_key") from exc
    return OperatorRunResponseSchema.model_validate(run)


@router.get("", response_model=OperatorRunListSchema)
async def list_runs(
    workspace_id: UUID,
    product_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OperatorRunListSchema:
    await _read_access(db, workspace_id, user.id)
    rows = list(
        await db.scalars(
            select(OperatorRun)
            .where(
                OperatorRun.workspace_id == workspace_id,
                OperatorRun.product_id == product_id,
                OperatorRun.run_kind.in_(("opportunity_detection", "weekly_growth")),
            )
            .order_by(OperatorRun.started_at.desc(), OperatorRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return OperatorRunListSchema(
        items=[OperatorRunResponseSchema.model_validate(row) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}", response_model=OperatorRunResponseSchema)
async def get_run(
    workspace_id: UUID, product_id: UUID, run_id: UUID, user: CurrentUserDep, db: DbDep
) -> OperatorRunResponseSchema:
    await _read_access(db, workspace_id, user.id)
    run = await db.scalar(
        select(OperatorRun).where(
            OperatorRun.workspace_id == workspace_id,
            OperatorRun.product_id == product_id,
            OperatorRun.id == run_id,
            OperatorRun.run_kind.in_(("opportunity_detection", "weekly_growth")),
        )
    )
    if run is None:
        raise NotFoundError("Operator run not found.", code="operator_run_not_found")
    return OperatorRunResponseSchema.model_validate(run)


@router.get("/{run_id}/evaluations", response_model=list[OpportunityEvaluationResponseSchema])
async def list_evaluations(
    workspace_id: UUID,
    product_id: UUID,
    run_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
    outcome: str | None = None,
    detector: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OpportunityEvaluationResponseSchema]:
    await get_run(workspace_id, product_id, run_id, user, db)
    statement = select(OpportunityEvaluation).where(
        OpportunityEvaluation.workspace_id == workspace_id,
        OpportunityEvaluation.product_id == product_id,
        OpportunityEvaluation.operator_run_id == run_id,
    )
    if outcome is not None:
        statement = statement.where(OpportunityEvaluation.outcome == outcome)
    if detector is not None:
        statement = statement.where(OpportunityEvaluation.detector_id == detector)
    rows = list(
        await db.scalars(
            statement.order_by(
                func.coalesce(OpportunityEvaluation.priority_score, -1).desc(),
                OpportunityEvaluation.id,
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return [OpportunityEvaluationResponseSchema.model_validate(row) for row in rows]


@router.get(
    "/{run_id}/evaluations/{evaluation_id}",
    response_model=OpportunityEvaluationResponseSchema,
)
async def get_evaluation(
    workspace_id: UUID,
    product_id: UUID,
    run_id: UUID,
    evaluation_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
) -> OpportunityEvaluationResponseSchema:
    await get_run(workspace_id, product_id, run_id, user, db)
    evaluation = await db.scalar(
        select(OpportunityEvaluation).where(
            OpportunityEvaluation.workspace_id == workspace_id,
            OpportunityEvaluation.product_id == product_id,
            OpportunityEvaluation.operator_run_id == run_id,
            OpportunityEvaluation.id == evaluation_id,
        )
    )
    if evaluation is None:
        raise NotFoundError("Evaluation not found.", code="evaluation_not_found")
    return OpportunityEvaluationResponseSchema.model_validate(evaluation)
