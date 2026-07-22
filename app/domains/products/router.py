from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    DbDep,
    TokenDep,
    get_browser_session,
    get_current_user,
    require_cookie_auth_csrf,
)
from app.domains.generation.dependencies import GenerationModelDep, GenerationProviderDep
from app.domains.products.generation import generate_product_context
from app.domains.products.repository import get_product_for_workspace, get_product_workspace
from app.domains.products.schemas import (
    ProductContextGenerationRequest,
    ProductContextGenerationResponse,
    ProductRepositoryAssignment,
    ProductRepositoryResponse,
    ProductRepositoryRoleDescriptionUpdate,
    ProductResponse,
    ProductWorkspaceResponse,
    ProductWrite,
)
from app.domains.products.service import (
    assign_repository,
    create_product,
    delete_product,
    remove_repository,
    update_product,
    update_repository_role_description,
)
from app.domains.users.models import User
from app.shared.exceptions import NotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}/products", tags=["products"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_product_mutation_user(request: Request, token: TokenDep, db: DbDep) -> User:
    if token is not None:
        return await get_current_user(request, token, db)
    browser_session = await get_browser_session(request, db)
    return (await require_cookie_auth_csrf(request, browser_session))[0]


ProductMutationUserDep = Annotated[User, Depends(get_product_mutation_user)]


@router.get("", response_model=ProductWorkspaceResponse)
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


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    workspace_id: UUID, product_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> ProductResponse:
    product = await get_product_for_workspace(db, workspace_id, product_id, current_user.id)
    if product is None:
        raise NotFoundError("Product not found.", code="product_not_found")
    return ProductResponse.model_validate(product)


@router.post("/{product_id}/context-generation", response_model=ProductContextGenerationResponse)
async def generate_product_context_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    payload: ProductContextGenerationRequest,
    current_user: ProductMutationUserDep,
    db: DbDep,
    provider: GenerationProviderDep,
    model: GenerationModelDep,
) -> ProductContextGenerationResponse:
    return await generate_product_context(
        db, workspace_id, product_id, current_user.id, payload, provider, model
    )


@router.put("/{product_id}", response_model=ProductResponse)
async def update_product_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    payload: ProductWrite,
    current_user: CurrentUserDep,
    db: DbDep,
) -> ProductResponse:
    product = await update_product(db, workspace_id, product_id, current_user.id, payload)
    return ProductResponse.model_validate(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_endpoint(
    workspace_id: UUID, product_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> Response:
    await delete_product(db, workspace_id, product_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{product_id}/repositories/{repo_id}", response_model=ProductRepositoryResponse)
async def assign_repository_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    payload: ProductRepositoryAssignment,
    current_user: CurrentUserDep,
    db: DbDep,
) -> ProductRepositoryResponse:
    mapping = await assign_repository(
        db, workspace_id, product_id, repo_id, current_user.id, payload
    )
    return ProductRepositoryResponse.model_validate(mapping)


@router.delete("/{product_id}/repositories/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_repository_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
) -> Response:
    await remove_repository(db, workspace_id, product_id, repo_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{product_id}/repositories/{repo_id}/role-description",
    response_model=ProductRepositoryResponse,
)
async def update_repository_role_description_endpoint(
    workspace_id: UUID,
    product_id: UUID,
    repo_id: UUID,
    payload: ProductRepositoryRoleDescriptionUpdate,
    current_user: CurrentUserDep,
    db: DbDep,
) -> ProductRepositoryResponse:
    mapping = await update_repository_role_description(
        db, workspace_id, product_id, repo_id, current_user.id, payload
    )
    return ProductRepositoryResponse.model_validate(mapping)


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product_endpoint(
    workspace_id: UUID, payload: ProductWrite, current_user: CurrentUserDep, db: DbDep
) -> ProductResponse:
    product = await create_product(db, workspace_id, current_user.id, payload)
    return ProductResponse.model_validate(product)
