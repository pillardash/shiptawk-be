from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select

from app.api.deps import (
    DbDep,
    LLMExecutionServiceDep,
    MutationUserDep,
    get_current_user,
)
from app.modules.identity.models.users import User
from app.modules.operator.models import MarketingPlan, OperatorRun, PlanAction
from app.modules.products.api.product_profile_router import product_profile_router
from app.modules.products.api.website_intelligence_router import website_intelligence_router
from app.modules.products.models import WebsiteSource, product_repositories
from app.modules.products.repositories.product_repository import (
    get_product_for_workspace,
    get_product_workspace,
)
from app.modules.products.schemas.product_schema import (
    ProductContextGenerationRequest,
    ProductContextGenerationResponse,
    ProductOperatorSummary,
    ProductOperatorSummaryList,
    ProductRepositoryAssignment,
    ProductRepositoryResponse,
    ProductRepositoryRoleDescriptionUpdate,
    ProductResponse,
    ProductWorkspaceResponse,
    ProductWrite,
)
from app.modules.products.services.activation_service import (
    projected_next_schedule,
    read_activation_state,
)
from app.modules.products.services.product_generation_service import generate_product_context
from app.modules.products.services.product_service import (
    assign_repository,
    create_product,
    delete_product,
    remove_repository,
    update_product,
    update_repository_role_description,
)
from app.modules.search_intelligence.models import SearchSource
from app.shared.exceptions import NotFoundError

product_router = APIRouter(prefix="/workspaces/{workspace_id}/products", tags=["products"])
product_router.include_router(product_profile_router)
product_router.include_router(website_intelligence_router)
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@product_router.get("", response_model=ProductWorkspaceResponse)
async def list_product_workspace(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> ProductWorkspaceResponse:
    workspace = await get_product_workspace(db, workspace_id, current_user.id)
    if workspace is None:
        raise NotFoundError("Product workspace not found.", code="product_workspace_not_found")
    products, product_repositories = workspace
    return ProductWorkspaceResponse(
        products=[ProductResponse.model_validate(product) for product in products],
        product_repositories=[
            ProductRepositoryResponse.model_validate(mapping) for mapping in product_repositories
        ],
    )


@product_router.get("/operator-summaries", response_model=ProductOperatorSummaryList)
async def list_product_operator_summaries(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> ProductOperatorSummaryList:
    workspace = await get_product_workspace(db, workspace_id, current_user.id)
    if workspace is None:
        raise NotFoundError("Product workspace not found.", code="product_workspace_not_found")
    products, _ = workspace
    activation = await read_activation_state(db, workspace_id)
    product_ids = [product.id for product in products]
    if not product_ids:
        return ProductOperatorSummaryList(items=[])
    websites = {
        product_id: (status, crawled)
        for product_id, status, crawled in (
            await db.execute(
                select(
                    WebsiteSource.product_id,
                    WebsiteSource.status,
                    WebsiteSource.last_successful_crawl_at,
                ).where(
                    WebsiteSource.workspace_id == workspace_id,
                    WebsiteSource.product_id.in_(product_ids),
                )
            )
        ).all()
    }
    searches = {
        product_id: (status, data_date)
        for product_id, status, data_date in (
            await db.execute(
                select(
                    SearchSource.product_id,
                    SearchSource.status,
                    SearchSource.latest_successful_data_date,
                ).where(
                    SearchSource.workspace_id == workspace_id,
                    SearchSource.product_id.in_(product_ids),
                )
            )
        ).all()
    }
    repository_ids = set(
        await db.scalars(
            select(product_repositories.c.product_id).where(
                product_repositories.c.workspace_id == workspace_id,
                product_repositories.c.product_id.in_(product_ids),
            )
        )
    )
    run_rank = (
        select(
            OperatorRun.product_id,
            OperatorRun.id,
            OperatorRun.status,
            func.row_number()
            .over(partition_by=OperatorRun.product_id, order_by=OperatorRun.started_at.desc())
            .label("rank"),
        )
        .where(
            OperatorRun.workspace_id == workspace_id,
            OperatorRun.product_id.in_(product_ids),
            OperatorRun.run_kind == "weekly_growth",
        )
        .subquery()
    )
    runs = {
        product_id: (run_id, run_status)
        for product_id, run_id, run_status in (
            await db.execute(
                select(run_rank.c.product_id, run_rank.c.id, run_rank.c.status).where(
                    run_rank.c.rank == 1
                )
            )
        ).all()
    }
    plan_rank = (
        select(
            MarketingPlan.product_id,
            MarketingPlan.id,
            MarketingPlan.plan_revision,
            func.row_number()
            .over(partition_by=MarketingPlan.product_id, order_by=MarketingPlan.finalized_at.desc())
            .label("rank"),
        )
        .where(
            MarketingPlan.workspace_id == workspace_id,
            MarketingPlan.product_id.in_(product_ids),
            MarketingPlan.status == "ready",
        )
        .subquery()
    )
    plans = {
        product_id: (plan_id, revision)
        for product_id, plan_id, revision in (
            await db.execute(
                select(plan_rank.c.product_id, plan_rank.c.id, plan_rank.c.plan_revision).where(
                    plan_rank.c.rank == 1
                )
            )
        ).all()
    }
    action_counts = {
        product_id: (int(open_count or 0), int(approval_count or 0))
        for product_id, open_count, approval_count in (
            await db.execute(
                select(
                    PlanAction.product_id,
                    func.count().filter(PlanAction.status.in_(("pending", "ready", "executing"))),
                    func.count().filter(PlanAction.approval_status == "pending"),
                )
                .where(
                    PlanAction.workspace_id == workspace_id, PlanAction.product_id.in_(product_ids)
                )
                .group_by(PlanAction.product_id)
            )
        ).all()
    }
    items = []
    for product in products:
        selected = activation.product is not None and activation.product.id == product.id
        blockers = list(activation.run_blockers) if selected else ["activation_product_mismatch"]
        website = websites.get(product.id)
        search = searches.get(product.id)
        run_id, run_status = runs.get(product.id, (None, None))
        plan_id, plan_revision = plans.get(product.id, (None, None))
        open_count, approval_count = action_counts.get(product.id, (0, 0))
        items.append(
            ProductOperatorSummary(
                product_id=product.id,
                product_name=product.name,
                ready=not blockers,
                blockers=blockers,
                repository_ready=(
                    bool(activation.monitored_repository_count)
                    if selected
                    else product.id in repository_ids
                ),
                website_status=(
                    "healthy"
                    if activation.website_ready
                    else "not_connected"
                    if activation.website_source is None
                    else "stale"
                )
                if selected
                else (
                    "not_connected"
                    if website is None
                    else "healthy"
                    if str(website[0]) == "active" and website[1]
                    else "stale"
                ),
                search_status=(
                    "healthy"
                    if activation.search_connected
                    else "not_connected"
                    if not activation.search_ready
                    else "reduced"
                )
                if selected
                else (
                    "not_connected"
                    if search is None
                    else "healthy"
                    if str(search[0]) == "active" and search[1]
                    else "stale"
                ),
                latest_run_id=run_id,
                latest_run_status=run_status,
                latest_plan_id=plan_id,
                latest_plan_revision=plan_revision,
                open_action_count=open_count,
                awaiting_approval_count=approval_count,
                next_schedule=projected_next_schedule(product),
                next_setup_action=blockers[0] if blockers else None,
            )
        )
    return ProductOperatorSummaryList(items=items)


@product_router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    workspace_id: UUID, product_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> ProductResponse:
    product = await get_product_for_workspace(db, workspace_id, product_id, current_user.id)
    if product is None:
        raise NotFoundError("Product not found.", code="product_not_found")
    return ProductResponse.model_validate(product)


@product_router.post(
    "/{product_id}/context-generation", response_model=ProductContextGenerationResponse
)
async def generate_product_context_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    payload: ProductContextGenerationRequest,
    current_user: MutationUserDep,
    db: DbDep,
    llm: LLMExecutionServiceDep,
) -> ProductContextGenerationResponse:
    return await generate_product_context(
        db, workspace_id, product_id, current_user.id, payload, llm
    )


@product_router.put("/{product_id}", response_model=ProductResponse)
async def update_product_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    payload: ProductWrite,
    current_user: MutationUserDep,
    db: DbDep,
) -> ProductResponse:
    product = await update_product(db, workspace_id, product_id, current_user.id, payload)
    return ProductResponse.model_validate(product)


@product_router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_endpoint(
    workspace_id: UUID, product_id: UUID, current_user: MutationUserDep, db: DbDep
) -> Response:
    await delete_product(db, workspace_id, product_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@product_router.put(
    "/{product_id}/repositories/{repo_id}", response_model=ProductRepositoryResponse
)
async def assign_repository_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    payload: ProductRepositoryAssignment,
    current_user: MutationUserDep,
    db: DbDep,
) -> ProductRepositoryResponse:
    mapping = await assign_repository(
        db, workspace_id, product_id, repo_id, current_user.id, payload
    )
    return ProductRepositoryResponse.model_validate(mapping)


@product_router.delete(
    "/{product_id}/repositories/{repo_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_repository_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    current_user: MutationUserDep,
    db: DbDep,
) -> Response:
    await remove_repository(db, workspace_id, product_id, repo_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@product_router.patch(
    "/{product_id}/repositories/{repo_id}/role-description",
    response_model=ProductRepositoryResponse,
)
async def update_repository_role_description_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    payload: ProductRepositoryRoleDescriptionUpdate,
    current_user: MutationUserDep,
    db: DbDep,
) -> ProductRepositoryResponse:
    mapping = await update_repository_role_description(
        db, workspace_id, product_id, repo_id, current_user.id, payload
    )
    return ProductRepositoryResponse.model_validate(mapping)


@product_router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product_endpoint(
    workspace_id: UUID, payload: ProductWrite, current_user: MutationUserDep, db: DbDep
) -> ProductResponse:
    product = await create_product(db, workspace_id, current_user.id, payload)
    return ProductResponse.model_validate(product)
