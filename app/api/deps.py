from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decode_access_token, decode_access_token_claims, verify_csrf_token
from app.db.session import get_db
from app.modules.identity.models.users import User
from app.modules.identity.services.browser_oauth import get_bound_session
from app.modules.identity.services.users import get_active_user_by_id
from app.modules.llm.services.llm_execution_service import LLMExecutionService
from app.services.jobs.base import JobService
from app.shared.exceptions import UnauthorizedError

bearer_scheme = HTTPBearer(auto_error=False)
TokenDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


async def get_browser_session(request: Request, db: DbDep) -> tuple[User, UUID]:
    raw_token = request.cookies.get(get_settings().browser_access_cookie_name)
    claims = decode_access_token_claims(raw_token) if raw_token else None
    if claims is None:
        raise UnauthorizedError("Browser session is missing or expired.", code="missing_session")
    try:
        user_id = UUID(claims["sub"])
        session_id = UUID(claims["sid"])
    except ValueError as exc:
        raise UnauthorizedError("Invalid browser session.", code="invalid_session") from exc
    user, _, _, _ = await get_bound_session(db, user_id=user_id, session_id=session_id)
    return user, session_id


BrowserSessionDep = Annotated[tuple[User, UUID], Depends(get_browser_session)]


async def require_cookie_auth_csrf(
    request: Request, browser_session: BrowserSessionDep
) -> tuple[User, UUID]:
    settings = get_settings()
    if request.headers.get("origin") not in settings.resolved_browser_allowed_origins:
        from app.shared.exceptions import BadRequestError

        raise BadRequestError("Origin is not allowed.", code="invalid_origin")
    token = request.cookies.get(settings.browser_csrf_cookie_name)
    header = request.headers.get(settings.browser_csrf_header_name)
    if not token or token != header or not verify_csrf_token(token, str(browser_session[1])):
        from app.shared.exceptions import BadRequestError

        raise BadRequestError("Invalid CSRF token.", code="invalid_csrf_token")
    return browser_session


CookieAuthCsrfDep = Annotated[tuple[User, UUID], Depends(require_cookie_auth_csrf)]


async def get_current_user(request: Request, token: TokenDep, db: DbDep) -> User:
    if token is None:
        return (await get_browser_session(request, db))[0]
    subject = decode_access_token(token.credentials)
    if subject is None:
        raise UnauthorizedError("Invalid authentication credentials.", code="invalid_token")
    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise UnauthorizedError(
            "Invalid authentication credentials.",
            code="invalid_token",
        ) from exc
    return await get_active_user_by_id(db, user_id)


async def get_mutation_user(request: Request, token: TokenDep, db: DbDep) -> User:
    if token is not None:
        return await get_current_user(request, token, db)
    browser_session = await get_browser_session(request, db)
    return (await require_cookie_auth_csrf(request, browser_session))[0]


MutationUserDep = Annotated[User, Depends(get_mutation_user)]


def get_jobs(request: Request) -> JobService:
    return cast(JobService, request.app.state.jobs)


def get_llm_execution_service(request: Request) -> LLMExecutionService:
    return cast(LLMExecutionService, request.app.state.llm_execution_service)


LLMExecutionServiceDep = Annotated[LLMExecutionService, Depends(get_llm_execution_service)]
