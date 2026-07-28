from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import (
    DbDep,
    LLMExecutionServiceDep,
    MutationUserDep,
    get_current_user,
)
from app.modules.identity.models.users import User
from app.modules.products.api.product_profile_router import product_profile_router
from app.modules.products.api.website_intelligence_router import website_intelligence_router
from app.modules.products.repositories.product_repository import (
    get_product_for_workspace,
    get_product_workspace,
)
from app.modules.products.schemas.product_schema import (
    ProductContextGenerationRequest,
    ProductContextGenerationResponse,
    ProductRepositoryAssignment,
    ProductRepositoryResponse,
    ProductRepositoryRoleDescriptionUpdate,
    ProductResponse,
    ProductWorkspaceResponse,
    ProductWrite,
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
