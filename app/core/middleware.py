import time
from collections.abc import Awaitable, Callable
from ipaddress import ip_address, ip_network
from uuid import uuid4

from fastapi import status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.logging import reset_request_id, set_request_id
from app.core.rate_limit import RateLimitStore
from app.core.sentry import set_sentry_request_context
from app.shared.responses import ErrorResponse

REQUEST_ID_HEADER = "X-Request-ID"


def get_client_ip(request: Request, trusted_proxy_ips: list[str] | None = None) -> str:
    peer_ip = request.client.host if request.client else "unknown"
    if trusted_proxy_ips and is_trusted_proxy(peer_ip, trusted_proxy_ips):
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",", maxsplit=1)[0].strip()
    return peer_ip


def is_trusted_proxy(peer_ip: str, trusted_proxy_ips: list[str]) -> bool:
    if peer_ip in trusted_proxy_ips:
        return True
    try:
        parsed_peer_ip = ip_address(peer_ip)
    except ValueError:
        return False
    for trusted_proxy in trusted_proxy_ips:
        try:
            if parsed_peer_ip in ip_network(trusted_proxy, strict=False):
                return True
        except ValueError:
            if peer_ip == trusted_proxy:
                return True
    return False


class RequestBodyTooLargeError(Exception):
    pass


def middleware_error_response(
    request: Request,
    *,
    status_code: int,
    detail: str,
    code: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", "-"))
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(detail=detail, code=code, request_id=request_id).model_dump(
            by_alias=True
        ),
        headers=headers,
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
        request.state.request_id = request_id
        set_sentry_request_context(request_id)
        token = set_request_id(request_id)

        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            reset_request_id(token)


class ProcessTimeMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time"] = f"{time.perf_counter() - start:.6f}"
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        if not self.settings.security_headers_enabled:
            return response
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", self.settings.referrer_policy)
        response.headers.setdefault("Permissions-Policy", self.settings.permissions_policy)
        if self.settings.hsts_is_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={self.settings.hsts_max_age_seconds}; includeSubDomains",
            )
        return response


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        content_length = request.headers.get("content-length")
        try:
            request_size = int(content_length) if content_length else 0
        except ValueError:
            response = middleware_error_response(
                request,
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length header.",
                code="invalid_content_length",
            )
            await response(scope, receive, send)
            return
        if request_size > self.max_body_bytes:
            response = middleware_error_response(
                request,
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Request body is too large.",
                code="request_body_too_large",
            )
            await response(scope, receive, send)
            return

        received_size = 0

        async def limited_receive() -> Message:
            nonlocal received_size
            message = await receive()
            if message["type"] == "http.request":
                received_size += len(message.get("body", b""))
                if received_size > self.max_body_bytes:
                    raise RequestBodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLargeError:
            response = middleware_error_response(
                request,
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Request body is too large.",
                code="request_body_too_large",
            )
            await response(scope, receive, send)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        store: RateLimitStore,
        limit: int,
        window_seconds: int,
        trusted_proxy_ips: list[str],
    ) -> None:
        super().__init__(app)
        self.store = store
        self.limit = limit
        self.window_seconds = window_seconds
        self.trusted_proxy_ips = trusted_proxy_ips

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        client_ip = get_client_ip(request, self.trusted_proxy_ips)
        key = f"http:{client_ip}:{request.method}:{request.url.path}"
        result = self.store.hit(key, limit=self.limit, window_seconds=self.window_seconds)
        if not result.allowed:
            return middleware_error_response(
                request,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests.",
                code="rate_limited",
                headers={"Retry-After": str(result.retry_after_seconds)},
            )
        return await call_next(request)
