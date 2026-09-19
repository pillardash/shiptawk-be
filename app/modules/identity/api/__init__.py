from app.modules.identity.api.auth import router as auth_router
from app.modules.identity.api.browser_router import router as browser_router
from app.modules.identity.api.router import router as identity_router

__all__ = ["auth_router", "browser_router", "identity_router"]
