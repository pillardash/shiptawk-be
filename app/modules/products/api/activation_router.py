from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status

from app.api.deps import DbDep, MutationUserDep, get_current_user
from app.core.config import get_settings
from app.modules.identity.models.users import User
from app.modules.operator.models import OperatorRun
from app.modules.operator.policies import WRITE_ROLES, require_role
from app.modules.products.schemas.activation_schema import (
    ActivationCommand,
    ActivationFinalizeCommand,
    ActivationFinalizeResponse,
    ActivationProductCommand,
    ActivationProjection,
    ActivationReportSummary,
    ActivationRunSummary,
    ActivationStageName,
    ActivationStageProjection,
    ActivationStageStatus,
    CapabilityReadiness,
    CapabilityStatus,
    EvidenceMode,
    RepositoryEvidenceModeCommand,
    SearchModeCommand,
    SourceCapability,
)
from app.modules.products.services.activation_service import (
    REDUCED_CAPABILITY_ALLOWED,
    ActivationState,
    finalize_activation_and_start,
    projected_next_schedule,
    read_activation_state,
    set_activation_product,
    set_repository_evidence_mode,
    set_search_mode,
)
from app.modules.workspaces.services import require_active_workspace_role
from app.shared.exceptions import ConflictError, NotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}/activation", tags=["product-activation"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


async def _access(db: DbDep, workspace_id: UUID, actor_id: UUID) -> None:
    await require_active_workspace_role(
        db,
        workspace_id,
        actor_id,
        missing=NotFoundError("Activation not found.", code="activation_not_found"),
    )


def _run_summary(run: OperatorRun | None) -> ActivationRunSummary | None:
    if run is None:
        return None
    return ActivationRunSummary(
        id=run.id,
        status=run.status,
        current_stage=run.current_stage,
        canonical_href=f"/users/history/runs/{run.id}",
    )


def _projection(state: ActivationState) -> ActivationProjection:
    search_provider_available = get_settings().google_search_enabled
    product = state.product
    product_id = product.id if product else None
    product_href = f"/users/products/{product_id}" if product_id else "/users/products"
    stages: list[ActivationStageProjection] = []
    stages.append(
        ActivationStageProjection(
            stage=ActivationStageName.product,
            status=ActivationStageStatus.complete if product else ActivationStageStatus.blocked,
            blockers=[] if product else ["activation_product_required"],
            allowed_commands=[] if product else [ActivationCommand.select_product],
            canonical_href=product_href,
            completed_at=product.created_at if product else None,
        )
    )
    repository_mode = product.repository_evidence_mode if product else None
    stages.append(
        ActivationStageProjection(
            stage=ActivationStageName.profile,
            status=ActivationStageStatus.complete
            if state.approved_profile
            else ActivationStageStatus.blocked,
            blockers=[] if state.approved_profile else ["approved_product_profile_required"],
            allowed_commands=[] if state.approved_profile else [ActivationCommand.approve_profile],
            canonical_href=f"{product_href}/profile" if product else "/users/products",
            completed_at=state.approved_profile.approved_at if state.approved_profile else None,
        )
    )
    search_blockers = []
    search_commands = []
    if not state.website_ready:
        search_blockers.append("successful_website_crawl_required")
        if state.website_source is None:
            search_commands.append(ActivationCommand.connect_website)
        else:
            search_commands.append(ActivationCommand.crawl_website)
    if not state.search_ready:
        search_blockers.append("search_connection_or_explicit_reduced_mode_required")
        if search_provider_available:
            search_commands.append(ActivationCommand.connect_search)
        if REDUCED_CAPABILITY_ALLOWED:
            search_commands.append(ActivationCommand.defer_search)
    stages.append(
        ActivationStageProjection(
            stage=ActivationStageName.search,
            status=ActivationStageStatus.complete
            if not search_blockers and product
            else ActivationStageStatus.blocked,
            blockers=search_blockers,
            allowed_commands=search_commands,
            canonical_href=f"{product_href}/search" if product else "/users/products",
            completed_at=(
                product.search_mode_decided_at if product and state.search_ready else None
            ),
        )
    )
    first_status = ActivationStageStatus.pending
    first_blockers = list(state.source_blockers)
    first_commands: list[ActivationCommand] = []
    if state.latest_report:
        first_status = ActivationStageStatus.complete
        first_commands = [ActivationCommand.view_report]
    elif state.active_run:
        first_status = ActivationStageStatus.in_progress
        first_commands = [ActivationCommand.view_run]
    elif first_blockers:
        first_status = ActivationStageStatus.blocked
    else:
        first_commands = [ActivationCommand.finalize_and_start]
    stages.append(
        ActivationStageProjection(
            stage=ActivationStageName.first_run,
            status=first_status,
            blockers=first_blockers,
            allowed_commands=first_commands,
            canonical_href=(
                f"/users/history/runs/{state.active_run.id}"
                if state.active_run
                else f"{product_href}/reports"
                if product
                else "/users/products"
            ),
            completed_at=state.latest_report.finalized_at if state.latest_report else None,
        )
    )
    report = state.latest_report
    latest_report = (
        ActivationReportSummary(
            run_id=report.operator_run_id,
            plan_id=report.id,
            revision=report.plan_revision or 1,
            period_start=report.period_start,
            period_end=report.period_end,
            finalized_at=report.finalized_at or report.updated_at,
            canonical_href=f"/users/products/{product_id}/reports?planId={report.id}",
        )
        if report and report.operator_run_id
        else None
    )
    current_status = state.current_run.status if state.current_run else "not_started"
    evidence_mode = None
    if product and repository_mode == "deferred":
        evidence_mode = EvidenceMode.website_search
    elif product and repository_mode == "connected":
        evidence_mode = (
            EvidenceMode.website_search_github
            if state.search_connected
            else EvidenceMode.website_github
        )
    required_blockers = list(state.source_blockers)
    github_required = repository_mode == "connected"
    sources = [
        SourceCapability(
            source="website",
            required=True,
            status=CapabilityStatus.ready
            if state.website_ready
            else CapabilityStatus.not_configured,
        ),
        SourceCapability(
            source="search",
            required=not (
                github_required and product and product.search_mode == "deferred_reduced"
            ),
            status=CapabilityStatus.ready
            if state.search_connected
            else CapabilityStatus.not_configured,
        ),
        SourceCapability(
            source="github",
            required=github_required,
            status=CapabilityStatus.ready
            if state.monitored_repository_count
            else CapabilityStatus.not_configured,
            limitation=(
                "Shipping-based recommendations are unavailable without a monitored repository."
                if not state.monitored_repository_count
                else None
            ),
        ),
    ]
    search_classes = [
        "high_impressions_low_ctr",
        "ranking_within_reach",
        "search_decline",
        "search_demand_without_dedicated_content",
        "existing_page_needs_improvement",
    ]
    available_classes = (
        list(search_classes) if state.website_ready and state.search_connected else []
    )
    unavailable_classes = [] if state.monitored_repository_count else ["shipped_but_not_marketed"]
    if state.monitored_repository_count:
        available_classes.append("shipped_but_not_marketed")
    return ActivationProjection(
        workspace_id=state.workspace.id,
        activation_product_id=product_id,
        onboarding_complete=state.workspace.onboarding_completed,
        search_mode=product.search_mode if product else None,
        repository_evidence_decision=repository_mode,
        readiness=(
            CapabilityReadiness(
                evidence_mode=evidence_mode,
                sources=sources,
                required_blockers=required_blockers,
                source_recovery=[],
                optional_enhancements=(
                    ["Add shipping evidence"] if repository_mode == "deferred" else []
                ),
                available_recommendation_classes=available_classes,
                unavailable_recommendation_classes=unavailable_classes,
            )
            if product
            else None
        ),
        reduced_capability_allowed=REDUCED_CAPABILITY_ALLOWED,
        search_provider_available=search_provider_available,
        stages=stages,
        active_run=_run_summary(state.active_run),
        current_run=_run_summary(state.current_run),
        latest_finalized_report=latest_report,
        first_run_state=current_status,
        next_schedule=projected_next_schedule(product) if product else None,
        canonical_destination=f"/users/today?productId={product_id}"
        if product
        else "/users/products",
    )


@router.get("", response_model=ActivationProjection)
async def get_activation(
    workspace_id: UUID, user: CurrentUserDep, db: DbDep
) -> ActivationProjection:
    await _access(db, workspace_id, user.id)
    return _projection(await read_activation_state(db, workspace_id))


@router.put("/product", response_model=ActivationProjection)
async def put_activation_product(
    workspace_id: UUID,
    payload: ActivationProductCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> ActivationProjection:
    await _access(db, workspace_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    await set_activation_product(
        db,
        workspace_id=workspace_id,
        product_id=payload.product_id,
        idempotency_key=idempotency_key,
    )
    return _projection(await read_activation_state(db, workspace_id))


@router.put("/search-mode", response_model=ActivationProjection)
async def put_search_mode(
    workspace_id: UUID,
    payload: SearchModeCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> ActivationProjection:
    await _access(db, workspace_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    state = await read_activation_state(db, workspace_id)
    if state.product is None:
        raise ConflictError(
            "Select an activation product first.", code="activation_product_required"
        )
    await set_search_mode(
        db,
        workspace_id=workspace_id,
        product_id=state.product.id,
        mode=payload.mode.value,
        idempotency_key=idempotency_key,
    )
    return _projection(await read_activation_state(db, workspace_id))


@router.put("/repository-evidence-mode", response_model=ActivationProjection)
async def put_repository_evidence_mode(
    workspace_id: UUID,
    payload: RepositoryEvidenceModeCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> ActivationProjection:
    await _access(db, workspace_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    state = await read_activation_state(db, workspace_id)
    if state.product is None:
        raise ConflictError(
            "Select an activation product first.", code="activation_product_required"
        )
    await set_repository_evidence_mode(
        db,
        workspace_id=workspace_id,
        product_id=state.product.id,
        mode=payload.mode.value,
        idempotency_key=idempotency_key,
    )
    return _projection(await read_activation_state(db, workspace_id))


@router.post(
    "/finalize-and-start",
    response_model=ActivationFinalizeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def finalize_and_start(
    workspace_id: UUID,
    payload: ActivationFinalizeCommand,
    user: MutationUserDep,
    db: DbDep,
    idempotency_key: IdempotencyKey,
) -> ActivationFinalizeResponse:
    await _access(db, workspace_id, user.id)
    await require_role(db, workspace_id, user.id, WRITE_ROLES)
    run, replayed = await finalize_activation_and_start(
        db,
        workspace_id=workspace_id,
        actor_id=user.id,
        idempotency_key=idempotency_key,
        values=payload.model_dump(mode="json", exclude_unset=True),
    )
    activation = _projection(await read_activation_state(db, workspace_id))
    run_projection = _run_summary(run)
    assert run_projection is not None
    return ActivationFinalizeResponse(
        replayed=replayed,
        run=run_projection,
        canonical_destination=activation.canonical_destination,
        activation=activation,
    )
