from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.domains.users.models import User
from app.domains.users.service import get_active_user_by_id
from app.services.jobs.base import JobService
from app.shared.exceptions import UnauthorizedError

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
TokenDep = Annotated[str | None, Depends(oauth2_scheme)]
DbDep = Annotated[Session, Depends(get_db)]


def get_current_user(token: TokenDep, db: DbDep) -> User:
    if token is None:
        raise UnauthorizedError(
            "Authentication credentials were not provided.",
            code="missing_token",
        )
    subject = decode_access_token(token)
    if subject is None:
        raise UnauthorizedError("Invalid authentication credentials.", code="invalid_token")
    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise UnauthorizedError(
            "Invalid authentication credentials.",
            code="invalid_token",
        ) from exc
    return get_active_user_by_id(db, user_id)


def get_jobs(request: Request) -> JobService:
    return cast(JobService, request.app.state.jobs)
