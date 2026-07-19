from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import DbDep, get_current_user
from app.domains.products.repository import get_product_for_workspace
from app.domains.products.schemas import ProductResponse
from app.domains.users.models import User
from app.shared.exceptions import NotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}/products", tags=["products"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    workspace_id: UUID, product_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> ProductResponse:
    product = await get_product_for_workspace(db, workspace_id, product_id, current_user.id)
    if product is None:
        raise NotFoundError("Product not found.", code="product_not_found")
    return ProductResponse.model_validate(product)
