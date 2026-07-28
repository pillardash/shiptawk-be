from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import router as api_router
from app.api.v1.health import health_check
from app.bootstrap.infrastructure import InfrastructureResources
from app.core.config import Settings
from app.core.exception_handlers import register_exception_handlers
from app.core.middleware import (
    BodySizeLimitMiddleware,
    BrowserAuthNoStoreMiddleware,
    ProcessTimeMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.rate_limit import create_rate_limit_store

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_fastapi(settings: Settings, lifespan: Lifespan) -> FastAPI:
    return FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Shiptawk AI marketing operator API",
        debug=settings.debug,
        openapi_url=settings.openapi_url,
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
        lifespan=lifespan,
    )


def register_application(
    app: FastAPI, settings: Settings, infrastructure: InfrastructureResources
) -> None:
    app.add_middleware(
        BrowserAuthNoStoreMiddleware,
        path_prefix=f"{settings.api_v1_prefix}/auth/browser",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_strings,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    if settings.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            store=create_rate_limit_store(infrastructure.cache),
            limit=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
            trusted_proxy_ips=settings.trusted_proxy_ips,
        )
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.max_request_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(ProcessTimeMiddleware)
    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)
    app.add_api_route("/health", health_check, methods=["GET"], tags=["health"])
    app.include_router(api_router, prefix=settings.api_v1_prefix)
