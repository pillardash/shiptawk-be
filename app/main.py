from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import router as api_router
from app.api.v1.health import health_check
from app.cache.dependencies import create_cache
from app.core.config import get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    BodySizeLimitMiddleware,
    ProcessTimeMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.rate_limit import create_rate_limit_store
from app.core.sentry import configure_sentry
from app.services.jobs import create_job_service
from app.services.storage.base import create_storage_service


def create_app() -> FastAPI:
    settings = get_settings()
    configure_sentry(settings)
    cache = create_cache(settings)
    job_service = create_job_service(settings)
    storage_service = create_storage_service(settings)
    configure_logging(level=settings.log_level, log_format=settings.log_format)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Reusable FastAPI backend boilerplate",
        debug=settings.debug,
        openapi_url=settings.openapi_url,
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
    )
    app.state.cache = cache
    app.state.jobs = job_service
    app.state.storage = storage_service

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
            store=create_rate_limit_store(cache),
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

    return app


app = create_app()
