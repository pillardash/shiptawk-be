from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integrations.models import IntegrationConnection
from app.modules.integrations.providers.github import GitHubInstallation, GitHubRepository
from app.modules.integrations.repositories import revoke_x_connections
from app.modules.repos.models import repos
from app.shared.exceptions import NotFoundError


async def disconnect_x_integration(db: AsyncSession, workspace_id: UUID, actor_id: UUID) -> None:
    await revoke_x_connections(db, workspace_id, actor_id, datetime.now(UTC))
    await db.commit()


async def attach_github_installation(
    db: AsyncSession, workspace_id: UUID, installation: GitHubInstallation
) -> None:
    connection = await db.scalar(
        select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.provider == "github_app",
        )
    )
    if connection is None:
        db.add(
            IntegrationConnection(
                workspace_id=workspace_id,
                provider="github_app",
                external_account_id=str(installation.id),
                external_username=installation.account_login,
                credentials_ciphertext="server-managed-github-app",
                credential_key_version="none",
                idempotency_key=f"github-app:{installation.id}",
            )
        )
    else:
        connection.external_account_id = str(installation.id)
        connection.external_username = installation.account_login
        connection.status = "active"
    await db.commit()


async def sync_github_repositories(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    remote: list[GitHubRepository],
) -> None:
    for item in remote:
        existing = await db.execute(
            select(repos).where(
                repos.c.workspace_id == workspace_id,
                repos.c.repo_full_name == item.full_name,
            )
        )
        if existing.first() is None:
            await db.execute(
                insert(repos).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    user_id=user_id,
                    repo_name=item.name,
                    repo_full_name=item.full_name,
                    private=item.private,
                    tracked_branch=item.default_branch,
                    language=item.language,
                    description=item.description,
                    is_tracked=False,
                )
            )
        else:
            await db.execute(
                update(repos)
                .where(
                    repos.c.workspace_id == workspace_id,
                    repos.c.repo_full_name == item.full_name,
                )
                .values(
                    repo_name=item.name,
                    private=item.private,
                    tracked_branch=item.default_branch,
                    language=item.language,
                    description=item.description,
                )
            )
    await db.commit()


async def update_repository_fields(
    db: AsyncSession, workspace_id: UUID, repo_id: UUID, **values: object
) -> Any:
    row = (
        await db.execute(
            update(repos)
            .where(repos.c.id == repo_id, repos.c.workspace_id == workspace_id)
            .values(**values)
            .returning(repos)
        )
    ).first()
    if row is None:
        raise NotFoundError("Repository not found.", code="repository_not_found")
    await db.commit()
    return row
