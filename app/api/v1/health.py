from fastapi import APIRouter

from app.core.config import get_settings
from app.db.health import check_database_ready
from app.shared.exceptions import ServiceUnavailableError
from app.shared.responses import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        environment=settings.app_env,
        version=settings.app_version,
    )


@router.get("/ready", response_model=ReadinessResponse)
def readiness_check() -> ReadinessResponse:
    try:
        check_database_ready()
    except Exception as exc:
        raise ServiceUnavailableError(
            "Database is not ready.",
            code="database_unavailable",
        ) from exc

    return ReadinessResponse(status="ready")
