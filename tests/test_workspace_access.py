from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.workspaces.enums import WorkspaceRole
from app.modules.workspaces.services.access import (
    require_active_workspace_role,
    resolve_active_workspace_role,
)
from app.shared.exceptions import ForbiddenError, NotFoundError


@pytest.mark.asyncio
async def test_resolve_active_workspace_role_delegates_to_workspace_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    workspace_id = uuid4()
    user_id = uuid4()
    lookup = AsyncMock(return_value=WorkspaceRole.editor)
    monkeypatch.setattr("app.modules.workspaces.services.access.get_active_membership_role", lookup)

    role = await resolve_active_workspace_role(db, workspace_id, user_id)

    assert role is WorkspaceRole.editor
    lookup.assert_awaited_once_with(db, workspace_id, user_id)


@pytest.mark.asyncio
async def test_require_active_workspace_role_preserves_callers_missing_resource_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.workspaces.services.access.get_active_membership_role",
        AsyncMock(return_value=None),
    )
    expected = NotFoundError("Draft not found.", code="draft_not_found")

    with pytest.raises(NotFoundError) as raised:
        await require_active_workspace_role(AsyncMock(), uuid4(), uuid4(), missing=expected)

    assert raised.value is expected
    assert raised.value.code == "draft_not_found"


@pytest.mark.asyncio
async def test_require_active_workspace_role_defaults_to_workspace_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.workspaces.services.access.get_active_membership_role",
        AsyncMock(return_value=None),
    )

    with pytest.raises(ForbiddenError) as raised:
        await require_active_workspace_role(AsyncMock(), uuid4(), uuid4())

    assert raised.value.code == "workspace_access_denied"
