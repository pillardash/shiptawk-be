from unittest.mock import AsyncMock

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import get_mutation_user


@pytest.mark.asyncio
async def test_mutation_user_uses_bearer_auth_without_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = AsyncMock()
    db = AsyncMock()
    token = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    user = object()
    current_user = AsyncMock(return_value=user)
    browser_session = AsyncMock()
    cookie_csrf = AsyncMock()
    monkeypatch.setattr("app.api.deps.get_current_user", current_user)
    monkeypatch.setattr("app.api.deps.get_browser_session", browser_session)
    monkeypatch.setattr("app.api.deps.require_cookie_auth_csrf", cookie_csrf)

    result = await get_mutation_user(request, token, db)

    assert result is user
    current_user.assert_awaited_once_with(request, token, db)
    browser_session.assert_not_awaited()
    cookie_csrf.assert_not_awaited()


@pytest.mark.asyncio
async def test_mutation_user_requires_csrf_for_cookie_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = AsyncMock()
    db = AsyncMock()
    user = object()
    session = (user, object())
    browser_session = AsyncMock(return_value=session)
    cookie_csrf = AsyncMock(return_value=session)
    current_user = AsyncMock()
    monkeypatch.setattr("app.api.deps.get_current_user", current_user)
    monkeypatch.setattr("app.api.deps.get_browser_session", browser_session)
    monkeypatch.setattr("app.api.deps.require_cookie_auth_csrf", cookie_csrf)

    result = await get_mutation_user(request, None, db)

    assert result is user
    browser_session.assert_awaited_once_with(request, db)
    cookie_csrf.assert_awaited_once_with(request, session)
    current_user.assert_not_awaited()
