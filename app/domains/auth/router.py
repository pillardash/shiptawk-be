import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_jobs
from app.core.config import get_settings
from app.core.rate_limit import RateLimitStore, create_rate_limit_store
from app.core.security import create_access_token
from app.db.session import get_db
from app.domains.auth.models import AuthTokenPurpose
from app.domains.auth.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    EmailRequest,
    LogoutRequest,
    RefreshTokenRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)
from app.domains.auth.service import (
    change_password,
    check_login_throttle,
    check_password_reset_throttle,
    create_auth_token,
    create_refresh_session,
    record_login_failure,
    record_password_reset_attempt,
    refresh_session,
    reset_password,
    revoke_refresh_session,
    verify_email,
)
from app.domains.users.models import User
from app.domains.users.schemas import UserCreate, UserLogin, UserResponse
from app.domains.users.service import authenticate_user, register_user
from app.services.jobs.base import JobService
from app.shared.exceptions import UnauthorizedError
from app.shared.responses import MessageResponse

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)
DbDep = Annotated[Session, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_rate_limit_store(request: Request) -> RateLimitStore:
    return create_rate_limit_store(request.app.state.cache)


RateLimitStoreDep = Annotated[RateLimitStore, Depends(get_rate_limit_store)]
JobsDep = Annotated[JobService, Depends(get_jobs)]


def get_ip_address(request: Request) -> str | None:
    return request.client.host if request.client else None


def build_auth_response(
    db: Session,
    user: User,
    *,
    request: Request,
    device_name: str | None = None,
) -> AuthResponse:
    return AuthResponse(
        user=UserResponse.model_validate(user),
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_session(
            db,
            user,
            user_agent=request.headers.get("user-agent"),
            ip_address=get_ip_address(request),
            device_name=device_name,
        ),
    )


def build_frontend_url(path: str, **params: str) -> str:
    settings = get_settings()
    normalized_path = path if path.startswith("/") else f"/{path}"
    query = urlencode(params)
    return f"{settings.frontend_url}{normalized_path}?{query}"


def enqueue_job_safely(
    jobs: JobService,
    task_name: str,
    *,
    kwargs: dict[str, object],
) -> None:
    try:
        jobs.enqueue(task_name, kwargs=kwargs)
    except Exception:
        logger.exception("Failed to enqueue auth job")


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, request: Request, db: DbDep) -> AuthResponse:
    user = register_user(db, email=payload.email, password=payload.password)
    return build_auth_response(db, user, request=request, device_name=payload.device_name)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: UserLogin,
    request: Request,
    db: DbDep,
    rate_limit_store: RateLimitStoreDep,
) -> AuthResponse:
    ip_address = get_ip_address(request)
    check_login_throttle(store=rate_limit_store, email=payload.email, ip_address=ip_address)
    try:
        user = authenticate_user(db, email=payload.email, password=payload.password)
    except UnauthorizedError:
        record_login_failure(store=rate_limit_store, email=payload.email, ip_address=ip_address)
        raise
    return build_auth_response(db, user, request=request, device_name=payload.device_name)


@router.post("/refresh", response_model=AuthResponse)
def refresh(payload: RefreshTokenRequest, request: Request, db: DbDep) -> AuthResponse:
    user = refresh_session(db, payload.refresh_token)
    return build_auth_response(db, user, request=request, device_name=payload.device_name)


@router.post("/logout", response_model=MessageResponse)
def logout(payload: LogoutRequest, db: DbDep) -> MessageResponse:
    revoke_refresh_session(db, payload.refresh_token)
    return MessageResponse(message="Logged out successfully.")


@router.post("/change-password", response_model=MessageResponse)
def change_password_endpoint(
    payload: ChangePasswordRequest,
    current_user: CurrentUserDep,
    db: DbDep,
) -> MessageResponse:
    change_password(
        db,
        current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return MessageResponse(message="Password changed successfully.")


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(
    payload: EmailRequest,
    db: DbDep,
    jobs: JobsDep,
) -> MessageResponse:
    token = create_auth_token(db, email=payload.email, purpose=AuthTokenPurpose.password_reset)
    if token is not None:
        enqueue_job_safely(
            jobs,
            "email.send_password_reset",
            kwargs={
                "recipient_email": str(payload.email),
                "reset_url": build_frontend_url("/reset-password", token=token),
            },
        )
    return MessageResponse(
        message="If an account exists, password reset instructions will be sent."
    )


@router.post("/reset-password", response_model=MessageResponse)
def reset_password_endpoint(
    payload: ResetPasswordRequest,
    request: Request,
    db: DbDep,
    rate_limit_store: RateLimitStoreDep,
) -> MessageResponse:
    ip_address = get_ip_address(request)
    check_password_reset_throttle(
        store=rate_limit_store,
        token=payload.token,
        ip_address=ip_address,
    )
    record_password_reset_attempt(
        store=rate_limit_store,
        token=payload.token,
        ip_address=ip_address,
    )
    reset_password(db, token=payload.token, new_password=payload.new_password)
    return MessageResponse(message="Password reset successfully.")


@router.post("/email-verification/request", response_model=MessageResponse)
def request_email_verification(
    payload: EmailRequest,
    db: DbDep,
    jobs: JobsDep,
) -> MessageResponse:
    token = create_auth_token(db, email=payload.email, purpose=AuthTokenPurpose.email_verification)
    if token is not None:
        enqueue_job_safely(
            jobs,
            "email.send_verification",
            kwargs={
                "recipient_email": str(payload.email),
                "verification_url": build_frontend_url("/verify-email", token=token),
            },
        )
    return MessageResponse(
        message="If an account exists, email verification instructions will be sent."
    )


@router.post("/email-verification/verify", response_model=UserResponse)
def verify_email_endpoint(payload: VerifyEmailRequest, db: DbDep) -> UserResponse:
    user = verify_email(db, token=payload.token)
    return UserResponse.model_validate(user)


@router.get("/me", response_model=UserResponse)
def me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(current_user)
