from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select

from app.api.deps import (
    DbDep,
    TokenDep,
    get_browser_session,
    get_current_user,
    require_cookie_auth_csrf,
)
from app.domains.operator.models import (
    ApprovalRequest,
    ExecutionRun,
    MarketingPlan,
    MeasurementWindow,
    OperatorRun,
    Opportunity,
    PlanAction,
    VerificationRun,
)
from app.domains.operator.repository import (
    get_current_plan,
    get_plan_with_actions,
    get_resource,
    list_resources,
)
from app.domains.operator.schemas import (
    ApprovalDecision,
    ApprovalRequestResponse,
    ExecutionCreate,
    ExecutionResponse,
    MeasurementResponse,
    OperatorRunCreate,
    OperatorRunListResponse,
    OperatorRunResponse,
    OpportunityCreate,
    OpportunityListResponse,
    OpportunityResponse,
    PlanActionResponse,
    PlanCreate,
    PlanListResponse,
    PlanResponse,
    VerificationResponse,
)
from app.domains.operator.service import (
    create_opportunity,
    create_plan,
    decide_action,
    execute_action,
    run_operator,
)
from app.domains.users.models import User
from app.shared.exceptions import NotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["operator"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_operator_mutation_user(request: Request, token: TokenDep, db: DbDep) -> User:
    if token is not None:
        return await get_current_user(request, token, db)
    return (await require_cookie_auth_csrf(request, await get_browser_session(request, db)))[0]


OperatorMutationUserDep = Annotated[User, Depends(get_operator_mutation_user)]


def _plan_response(plan: MarketingPlan, actions: list[PlanAction]) -> PlanResponse:
    data = PlanResponse.model_validate(plan)
    return data.model_copy(
        update={"actions": [PlanActionResponse.model_validate(action) for action in actions]}
    )


@router.get("/opportunities", response_model=OpportunityListResponse)
async def list_opportunities(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> OpportunityListResponse:
    items = await list_resources(
        db,
        workspace_id,
        current_user.id,
        Opportunity,
        statement=select(Opportunity).order_by(
            Opportunity.created_at.desc(), Opportunity.id.desc()
        ),
    )
    if items is None:
        raise NotFoundError(
            "Opportunity workspace not found.", code="opportunity_workspace_not_found"
        )
    return OpportunityListResponse(
        items=[OpportunityResponse.model_validate(item) for item in items]
    )


@router.post(
    "/opportunities", response_model=OpportunityResponse, status_code=status.HTTP_201_CREATED
)
async def create_opportunity_endpoint(
    workspace_id: UUID,
    payload: OpportunityCreate,
    current_user: OperatorMutationUserDep,
    db: DbDep,
) -> OpportunityResponse:
    return OpportunityResponse.model_validate(
        await create_opportunity(db, workspace_id, current_user.id, payload)
    )


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityResponse)
async def get_opportunity_endpoint(
    workspace_id: UUID, opportunity_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> OpportunityResponse:
    item = await get_resource(db, workspace_id, opportunity_id, current_user.id, Opportunity)
    if item is None:
        raise NotFoundError("Opportunity not found.", code="opportunity_not_found")
    return OpportunityResponse.model_validate(item)


@router.get("/operator/runs", response_model=OperatorRunListResponse)
async def list_operator_runs(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> OperatorRunListResponse:
    items = await list_resources(db, workspace_id, current_user.id, OperatorRun)
    if items is None:
        raise NotFoundError("Operator workspace not found.", code="operator_workspace_not_found")
    return OperatorRunListResponse(
        items=[OperatorRunResponse.model_validate(item) for item in items]
    )


@router.post(
    "/operator/runs", response_model=OperatorRunResponse, status_code=status.HTTP_201_CREATED
)
async def run_operator_endpoint(
    workspace_id: UUID,
    payload: OperatorRunCreate,
    current_user: OperatorMutationUserDep,
    db: DbDep,
) -> OperatorRunResponse:
    return OperatorRunResponse.model_validate(
        await run_operator(db, workspace_id, current_user.id, payload)
    )


@router.get("/operator/runs/{run_id}", response_model=OperatorRunResponse)
async def get_operator_run(
    workspace_id: UUID, run_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> OperatorRunResponse:
    item = await get_resource(db, workspace_id, run_id, current_user.id, OperatorRun)
    if item is None:
        raise NotFoundError("Operator run not found.", code="operator_run_not_found")
    return OperatorRunResponse.model_validate(item)


@router.get("/plans", response_model=PlanListResponse)
async def list_plans(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> PlanListResponse:
    plans = await list_resources(db, workspace_id, current_user.id, MarketingPlan)
    if plans is None:
        raise NotFoundError("Plan workspace not found.", code="plan_workspace_not_found")
    responses = []
    for plan in plans:
        aggregate = await get_plan_with_actions(db, workspace_id, plan.id, current_user.id)
        assert aggregate is not None
        responses.append(_plan_response(*aggregate))
    return PlanListResponse(items=responses)


@router.post("/plans", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan_endpoint(
    workspace_id: UUID, payload: PlanCreate, current_user: OperatorMutationUserDep, db: DbDep
) -> PlanResponse:
    plan = await create_plan(db, workspace_id, current_user.id, payload)
    aggregate = await get_plan_with_actions(db, workspace_id, plan.id, current_user.id)
    assert aggregate is not None
    return _plan_response(*aggregate)


@router.get("/plans/current", response_model=PlanResponse)
async def get_current_plan_endpoint(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> PlanResponse:
    aggregate = await get_current_plan(db, workspace_id, current_user.id)
    if aggregate is None:
        raise NotFoundError("Current plan not found.", code="current_plan_not_found")
    return _plan_response(*aggregate)


@router.get("/plans/{plan_id}", response_model=PlanResponse)
async def get_plan_endpoint(
    workspace_id: UUID, plan_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> PlanResponse:
    aggregate = await get_plan_with_actions(db, workspace_id, plan_id, current_user.id)
    if aggregate is None:
        raise NotFoundError("Plan not found.", code="plan_not_found")
    return _plan_response(*aggregate)


@router.get("/plans/{plan_id}/actions", response_model=list[PlanActionResponse])
async def list_plan_actions(
    workspace_id: UUID, plan_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> list[PlanActionResponse]:
    aggregate = await get_plan_with_actions(db, workspace_id, plan_id, current_user.id)
    if aggregate is None:
        raise NotFoundError("Plan not found.", code="plan_not_found")
    return [PlanActionResponse.model_validate(action) for action in aggregate[1]]


@router.get("/plans/{plan_id}/actions/{action_id}", response_model=PlanActionResponse)
async def get_plan_action(
    workspace_id: UUID,
    plan_id: UUID,
    action_id: UUID,
    current_user: OperatorMutationUserDep,
    db: DbDep,
) -> PlanActionResponse:
    action = await get_resource(db, workspace_id, action_id, current_user.id, PlanAction)
    if action is None or action.plan_id != plan_id:
        raise NotFoundError("Plan action not found.", code="plan_action_not_found")
    return PlanActionResponse.model_validate(action)


@router.post("/plans/{plan_id}/actions/{action_id}/approve", response_model=ApprovalRequestResponse)
async def approve_action(
    workspace_id: UUID,
    plan_id: UUID,
    action_id: UUID,
    payload: ApprovalDecision,
    current_user: OperatorMutationUserDep,
    db: DbDep,
) -> ApprovalRequestResponse:
    return ApprovalRequestResponse.model_validate(
        await decide_action(
            db, workspace_id, plan_id, action_id, current_user.id, "approved", payload.reason
        )
    )


@router.post("/plans/{plan_id}/actions/{action_id}/reject", response_model=ApprovalRequestResponse)
async def reject_action(
    workspace_id: UUID,
    plan_id: UUID,
    action_id: UUID,
    payload: ApprovalDecision,
    current_user: OperatorMutationUserDep,
    db: DbDep,
) -> ApprovalRequestResponse:
    return ApprovalRequestResponse.model_validate(
        await decide_action(
            db, workspace_id, plan_id, action_id, current_user.id, "rejected", payload.reason
        )
    )


@router.post(
    "/plans/{plan_id}/actions/{action_id}/execute",
    response_model=ExecutionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def execute_action_endpoint(
    workspace_id: UUID,
    plan_id: UUID,
    action_id: UUID,
    payload: ExecutionCreate,
    current_user: CurrentUserDep,
    db: DbDep,
) -> ExecutionResponse:
    return ExecutionResponse.model_validate(
        await execute_action(
            db, workspace_id, plan_id, action_id, current_user.id, payload.idempotency_key
        )
    )


async def _list_foundation_resource[
    T: ApprovalRequest | ExecutionRun | VerificationRun | MeasurementWindow
](
    db: DbDep,
    workspace_id: UUID,
    user_id: UUID,
    model: type[T],
) -> list[T]:
    items = await list_resources(db, workspace_id, user_id, model)
    if items is None:
        raise NotFoundError("Operator resource not found.", code="operator_resource_not_found")
    return items


@router.get("/approval-requests", response_model=list[ApprovalRequestResponse])
async def list_approval_requests(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> list[ApprovalRequestResponse]:
    items = await _list_foundation_resource(db, workspace_id, current_user.id, ApprovalRequest)
    return [ApprovalRequestResponse.model_validate(item) for item in items]


@router.get("/approval-requests/{approval_id}", response_model=ApprovalRequestResponse)
async def get_approval_request(
    workspace_id: UUID, approval_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> ApprovalRequestResponse:
    item = await get_resource(db, workspace_id, approval_id, current_user.id, ApprovalRequest)
    if item is None:
        raise NotFoundError("Approval request not found.", code="approval_request_not_found")
    return ApprovalRequestResponse.model_validate(item)


@router.get("/executions", response_model=list[ExecutionResponse])
async def list_executions(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> list[ExecutionResponse]:
    items = await _list_foundation_resource(db, workspace_id, current_user.id, ExecutionRun)
    return [ExecutionResponse.model_validate(item) for item in items]


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    workspace_id: UUID, execution_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> ExecutionResponse:
    item = await get_resource(db, workspace_id, execution_id, current_user.id, ExecutionRun)
    if item is None:
        raise NotFoundError("Execution not found.", code="execution_not_found")
    return ExecutionResponse.model_validate(item)


@router.get("/verifications", response_model=list[VerificationResponse])
async def list_verifications(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> list[VerificationResponse]:
    items = await _list_foundation_resource(db, workspace_id, current_user.id, VerificationRun)
    return [VerificationResponse.model_validate(item) for item in items]


@router.get("/verifications/{verification_id}", response_model=VerificationResponse)
async def get_verification(
    workspace_id: UUID, verification_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> VerificationResponse:
    item = await get_resource(db, workspace_id, verification_id, current_user.id, VerificationRun)
    if item is None:
        raise NotFoundError("Verification not found.", code="verification_not_found")
    return VerificationResponse.model_validate(item)


@router.get("/measurements", response_model=list[MeasurementResponse])
async def list_measurements(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> list[MeasurementResponse]:
    items = await _list_foundation_resource(db, workspace_id, current_user.id, MeasurementWindow)
    return [MeasurementResponse.model_validate(item) for item in items]


@router.get("/measurements/{measurement_id}", response_model=MeasurementResponse)
async def get_measurement(
    workspace_id: UUID, measurement_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> MeasurementResponse:
    item = await get_resource(db, workspace_id, measurement_id, current_user.id, MeasurementWindow)
    if item is None:
        raise NotFoundError("Measurement not found.", code="measurement_not_found")
    return MeasurementResponse.model_validate(item)
