from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select

from app.api.deps import DbDep, MutationUserDep, get_current_user
from app.modules.identity.models.users import User
from app.modules.operator.models import Opportunity, OpportunityEvaluation, OpportunityEvidence
from app.modules.operator.policies import require_role
from app.modules.operator.policies.opportunity_transition_policy import (
    InvalidOpportunityTransitionError,
)
from app.modules.operator.schemas import OpportunityFeedbackCommand
from app.modules.operator.schemas.operator_api_schema import (
    OpportunityDismissalRequestSchema,
    OpportunityEvidenceResponseSchema,
    OpportunityFeedbackRequestSchema,
    OpportunityFeedbackResponseSchema,
    OpportunityListSchema,
    OpportunityResponseSchema,
)
from app.modules.operator.services.opportunity_feedback_service import (
    FeedbackIdempotencyConflictError,
    OpportunityNotFoundError,
    append_feedback,
    dismiss_opportunity,
)
from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.services import require_active_workspace_role
from app.shared.exceptions import BadRequestError, ConflictError, NotFoundError

router = APIRouter(
    prefix="/workspaces/{workspace_id}/products/{product_id}/opportunities",
    tags=["opportunities"],
)
CurrentUserDep = Annotated[User, Depends(get_current_user)]
DISMISS_ROLES = {
    WorkspaceRole.owner,
    WorkspaceRole.admin,
    WorkspaceRole.editor,
    WorkspaceRole.reviewer,
}


@router.get("", response_model=OpportunityListSchema)
async def list_opportunities(
    workspace_id: UUID,
    product_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    opportunity_type: Annotated[str | None, Query(alias="type")] = None,
    detector: str | None = None,
    min_priority: Annotated[float | None, Query(alias="minPriority", ge=0, le=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OpportunityListSchema:
    await require_active_workspace_role(db, workspace_id, user.id)
    statement = select(Opportunity).where(
        Opportunity.workspace_id == workspace_id, Opportunity.product_id == product_id
    )
    if status_filter is not None:
        statement = statement.where(Opportunity.status == status_filter)
    if opportunity_type is not None:
        statement = statement.where(Opportunity.opportunity_type == opportunity_type)
    if detector is not None:
        statement = statement.where(Opportunity.detector_id == detector)
    if min_priority is not None:
        statement = statement.where(Opportunity.priority_score >= min_priority)
    rows = list(
        await db.scalars(
            statement.order_by(
                Opportunity.priority_score.desc(), Opportunity.created_at.desc(), Opportunity.id
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return OpportunityListSchema(
        items=[OpportunityResponseSchema.model_validate(row) for row in rows],
        limit=limit,
        offset=offset,
    )


async def _get(
    db: DbDep, workspace_id: UUID, product_id: UUID, opportunity_id: UUID, user_id: UUID
) -> Opportunity:
    await require_active_workspace_role(
        db,
        workspace_id,
        user_id,
        missing=NotFoundError("Opportunity not found.", code="opportunity_not_found"),
    )
    opportunity = await db.scalar(
        select(Opportunity).where(
            Opportunity.workspace_id == workspace_id,
            Opportunity.product_id == product_id,
            Opportunity.id == opportunity_id,
        )
    )
    if opportunity is None:
        raise NotFoundError("Opportunity not found.", code="opportunity_not_found")
    return opportunity


@router.get("/{opportunity_id}", response_model=OpportunityResponseSchema)
async def get_opportunity(
    workspace_id: UUID, product_id: UUID, opportunity_id: UUID, user: CurrentUserDep, db: DbDep
) -> OpportunityResponseSchema:
    return OpportunityResponseSchema.model_validate(
        await _get(db, workspace_id, product_id, opportunity_id, user.id)
    )


@router.get("/{opportunity_id}/evidence", response_model=list[OpportunityEvidenceResponseSchema])
async def get_evidence(
    workspace_id: UUID, product_id: UUID, opportunity_id: UUID, user: CurrentUserDep, db: DbDep
) -> list[OpportunityEvidenceResponseSchema]:
    await _get(db, workspace_id, product_id, opportunity_id, user.id)
    rows = list(
        await db.scalars(
            select(OpportunityEvidence)
            .where(
                OpportunityEvidence.workspace_id == workspace_id,
                OpportunityEvidence.product_id == product_id,
                OpportunityEvidence.opportunity_id == opportunity_id,
            )
            .order_by(OpportunityEvidence.observed_at.desc(), OpportunityEvidence.id)
        )
    )
    return [OpportunityEvidenceResponseSchema.model_validate(row) for row in rows]


@router.get(
    "/{opportunity_id}/evidence/{evidence_id}",
    response_model=OpportunityEvidenceResponseSchema,
)
async def get_evidence_detail(
    workspace_id: UUID,
    product_id: UUID,
    opportunity_id: UUID,
    evidence_id: UUID,
    user: CurrentUserDep,
    db: DbDep,
) -> OpportunityEvidenceResponseSchema:
    await _get(db, workspace_id, product_id, opportunity_id, user.id)
    evidence = await db.scalar(
        select(OpportunityEvidence).where(
            OpportunityEvidence.workspace_id == workspace_id,
            OpportunityEvidence.product_id == product_id,
            OpportunityEvidence.opportunity_id == opportunity_id,
            OpportunityEvidence.id == evidence_id,
        )
    )
    if evidence is None:
        raise NotFoundError("Evidence not found.", code="opportunity_evidence_not_found")
    return OpportunityEvidenceResponseSchema.model_validate(evidence)


async def _feedback(
    *,
    dismiss: bool,
    workspace_id: UUID,
    product_id: UUID,
    opportunity_id: UUID,
    payload: OpportunityFeedbackRequestSchema | OpportunityDismissalRequestSchema,
    user: User,
    db: DbDep,
    idempotency_key: str,
) -> OpportunityFeedbackResponseSchema:
    await _get(db, workspace_id, product_id, opportunity_id, user.id)
    if dismiss:
        await require_role(db, workspace_id, user.id, DISMISS_ROLES)
    command = OpportunityFeedbackCommand(
        opportunity_id=opportunity_id,
        evaluation_id=await db.scalar(
            select(OpportunityEvaluation.id)
            .where(
                OpportunityEvaluation.workspace_id == workspace_id,
                OpportunityEvaluation.product_id == product_id,
                OpportunityEvaluation.accepted_opportunity_id == opportunity_id,
            )
            .order_by(OpportunityEvaluation.created_at.desc(), OpportunityEvaluation.id.desc())
            .limit(1)
        ),
        reason_code=payload.reason,
        usefulness=payload.usefulness,
        comment=payload.comment,
        evaluation_eligible=payload.evaluation_eligible,
    )
    try:
        result = await (dismiss_opportunity if dismiss else append_feedback)(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            actor_id=user.id,
            idempotency_key=idempotency_key,
            command=command,
        )
    except FeedbackIdempotencyConflictError as exc:
        raise ConflictError(str(exc), code="feedback_idempotency_conflict") from exc
    except InvalidOpportunityTransitionError as exc:
        raise ConflictError(str(exc), code="invalid_opportunity_transition") from exc
    except OpportunityNotFoundError as exc:
        raise NotFoundError(str(exc), code="opportunity_not_found") from exc
    except ValueError as exc:
        raise BadRequestError(str(exc), code="invalid_idempotency_key") from exc
    return OpportunityFeedbackResponseSchema.model_validate(result)


@router.post("/{opportunity_id}/dismiss", response_model=OpportunityFeedbackResponseSchema)
async def dismiss(
    workspace_id: UUID,
    product_id: UUID,
    opportunity_id: UUID,
    payload: OpportunityDismissalRequestSchema,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> OpportunityFeedbackResponseSchema:
    return await _feedback(
        dismiss=True,
        workspace_id=workspace_id,
        product_id=product_id,
        opportunity_id=opportunity_id,
        payload=payload,
        user=user,
        db=db,
        idempotency_key=idempotency_key,
    )


@router.post("/{opportunity_id}/feedback", response_model=OpportunityFeedbackResponseSchema)
async def feedback(
    workspace_id: UUID,
    product_id: UUID,
    opportunity_id: UUID,
    payload: OpportunityFeedbackRequestSchema,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> OpportunityFeedbackResponseSchema:
    return await _feedback(
        dismiss=False,
        workspace_id=workspace_id,
        product_id=product_id,
        opportunity_id=opportunity_id,
        payload=payload,
        user=user,
        db=db,
        idempotency_key=idempotency_key,
    )
