from fastapi import FastAPI

from app.bootstrap.application import create_fastapi, register_application
from app.bootstrap.resources import (
    bind_application_state,
    create_lifespan,
    create_resources,
)
from app.bootstrap.workflows import register_workflows
from app.core.config import get_settings
from app.core.logging import configure_ai_content_logging, configure_logging
from app.core.sentry import configure_sentry


def create_app() -> FastAPI:
    settings = get_settings()
    configure_sentry(settings)
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        file_enabled=settings.log_file_enabled,
        file_path=settings.log_file_path,
    )
    configure_ai_content_logging(
        enabled=settings.ai_content_log_enabled,
        file_path=settings.ai_content_log_path,
    )
    resources = create_resources(settings)
    app = create_fastapi(settings, create_lifespan(resources))
    bind_application_state(app, resources)
    register_application(app, settings, resources.infrastructure)
    register_workflows(app, settings, resources.workflows)
    return app


app = create_app()
