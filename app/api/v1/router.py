from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.domains.achievement_digests.router import router as achievement_digests_router
from app.domains.analytics.router import router as analytics_router
from app.domains.auth.browser_router import router as browser_auth_router
from app.domains.auth.router import router as auth_router
from app.domains.drafts.router import router as drafts_router
from app.domains.evidence.router import router as evidence_router
from app.domains.integrations.router import router as integrations_router
from app.domains.integrations.webhook_router import router as webhook_router
from app.domains.notifications.router import router as notifications_router
from app.domains.operator.router import router as operator_router
from app.domains.products.router import router as products_router
from app.domains.users.router import router as user_settings_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(browser_auth_router)
router.include_router(health_router)
router.include_router(achievement_digests_router)
router.include_router(analytics_router)
router.include_router(notifications_router)
router.include_router(products_router)
router.include_router(drafts_router)
router.include_router(evidence_router)
router.include_router(operator_router)
router.include_router(user_settings_router)
router.include_router(integrations_router)
router.include_router(webhook_router)
