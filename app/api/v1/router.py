from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.modules.achievement_digests.api.router import router as achievement_digests_router
from app.modules.analytics.api.router import browser_event_router
from app.modules.analytics.api.router import router as analytics_router
from app.modules.drafts.api.router import router as drafts_router
from app.modules.evidence.api.router import router as evidence_router
from app.modules.identity.api import browser_router as browser_auth_router
from app.modules.identity.api import identity_router
from app.modules.integrations.api.router import router as integrations_router
from app.modules.integrations.api.webhook_router import router as webhook_router
from app.modules.notifications.api.router import router as notifications_router
from app.modules.operator.api.operator_lookup_router import router as operator_lookup_router
from app.modules.operator.api.operator_run_router import router as operator_run_router
from app.modules.operator.api.opportunity_router import router as opportunity_router
from app.modules.operator.api.weekly_growth_router import router as weekly_growth_router
from app.modules.products.api.activation_router import router as activation_router
from app.modules.products.api.product_router import product_router
from app.modules.search_intelligence.api.search_connection_router import (
    product_search_router,
    search_callback_router,
)
from app.modules.search_intelligence.api.search_read_router import (
    search_read_router,
    workspace_search_router,
)
from app.modules.search_intelligence.api.search_sync_router import search_sync_router

router = APIRouter()
router.include_router(identity_router)
router.include_router(browser_auth_router)
router.include_router(health_router)
router.include_router(achievement_digests_router)
router.include_router(analytics_router)
router.include_router(browser_event_router)
router.include_router(notifications_router)
router.include_router(product_router)
router.include_router(activation_router)
router.include_router(drafts_router)
router.include_router(evidence_router)
router.include_router(operator_run_router)
router.include_router(operator_lookup_router)
router.include_router(opportunity_router)
router.include_router(weekly_growth_router)
router.include_router(integrations_router)
router.include_router(webhook_router)
router.include_router(product_search_router)
router.include_router(search_callback_router)
router.include_router(search_sync_router)
router.include_router(search_read_router)
router.include_router(workspace_search_router)
