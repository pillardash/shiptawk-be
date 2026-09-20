import logging
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ExceptionHandler

from app.core.config import get_settings
from app.core.sentry import capture_exception
from app.shared.exceptions import AppError
from app.shared.responses import ErrorResponse, ValidationErrorItem, ValidationErrorResponse
from app.shared.schemas import to_camel

logger = logging.getLogger(__name__)


def get_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "-"))


def error_payload(
    request: Request, detail: str, code: str, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = ErrorResponse(
        detail=detail,
        code=code,
        request_id=get_request_id(request),
    ).model_dump(by_alias=True)
    if metadata:
        payload["metadata"] = metadata
    return payload


def format_error_field(location: tuple[int | str, ...]) -> str:
    public_location = [part for part in location if part not in {"body", "query", "path"}]
    return ".".join(to_camel(str(part)) for part in public_location)


def validation_errors(exc: RequestValidationError) -> list[ValidationErrorItem]:
    return [
        ValidationErrorItem(
            field=format_error_field(tuple(error.get("loc", ()))),
            message=str(error.get("msg", "Invalid value")),
            type=str(error.get("type", "validation_error")),
        )
        for error in exc.errors()
    ]


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.log(
        logging.ERROR if exc.status_code >= 500 else logging.WARNING,
        "Handled application error method=%s path=%s status=%s code=%s detail=%s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.code,
        exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(request, exc.message, exc.code, exc.metadata),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = validation_errors(exc)
    logger.warning(
        "Request validation failed method=%s path=%s errors=%s",
        request.method,
        request.url.path,
        [item.model_dump() for item in errors],
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=ValidationErrorResponse(
            detail="Request validation failed.",
            code="validation_error",
            request_id=get_request_id(request),
            errors=errors,
        ).model_dump(by_alias=True),
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    settings = get_settings()
    detail = exc.detail if isinstance(exc.detail, str) else "HTTP error."
    if not settings.expose_errors and exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        detail = "Internal server error."
    code = "unauthorized" if exc.status_code == status.HTTP_401_UNAUTHORIZED else "http_error"
    logger.log(
        logging.ERROR if exc.status_code >= 500 else logging.WARNING,
        "HTTP error method=%s path=%s status=%s code=%s detail=%s",
        request.method,
        request.url.path,
        exc.status_code,
        code,
        detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(request, detail, code),
        headers=exc.headers,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled application error", exc_info=exc)
    capture_exception(exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_payload(request, "Internal server error.", "internal_server_error"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, cast(ExceptionHandler, app_error_handler))
    app.add_exception_handler(HTTPException, cast(ExceptionHandler, http_exception_handler))
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_error_handler),
    )
    app.add_exception_handler(Exception, cast(ExceptionHandler, unhandled_error_handler))
