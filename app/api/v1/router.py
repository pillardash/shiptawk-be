from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.domains.auth.router import router as auth_router
from app.domains.products.router import router as products_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(health_router)
router.include_router(products_router)
