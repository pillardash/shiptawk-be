import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid5

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.integrations.events import EventNormalizer, NormalizedEvent
from app.modules.integrations.models import (
    github_raw_event_consumers,
    github_raw_events,
    normalized_events,
)
from app.modules.integrations.policies.branches import should_process_tracked_branch
from app.modules.repos.models import repos
from app.services.github_webhook import decrypt_github_webhook_payload


@dataclass(frozen=True, slots=True)
class GitHubWorkflowContext:
    consumer_id: UUID
    raw_event_id: UUID
    workspace_id: UUID
    user_id: UUID
    repo_id: UUID
    repo_name: str
    repo_full_name: str
    repo_private: bool
    repo_description: str | None
    tracked_branch: str | None
    event_name: str
    payload: dict[str, object]


class GenerationWorkflow(Protocol):
    async def generate(
        self,
        context: GitHubWorkflowContext,
        event: NormalizedEvent,
        normalized_event_id: UUID,
    ) -> None: ...


class GitHubConsumerWorkflow:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        encryption_key: str,
        generation: GenerationWorkflow,
    ) -> None:
        self._sessions = sessions
        self._encryption_key = encryption_key
        self._generation = generation

    async def process(self, consumer_id: UUID) -> dict[str, object]:
        async with self._sessions() as db:
            context = await self._claim(db, consumer_id)
            if context is None:
                return {"status": "processed", "generated": False}
            try:
                event = EventNormalizer().normalize(
                    context.event_name, context.payload, context.repo_full_name
                )
                if event is None or not should_process_tracked_branch(
                    context.tracked_branch, event
                ):
                    await self._complete(db, consumer_id)
                    return {"status": "ignored", "generated": False}
                normalized_event_id = await self._persist_normalized(db, context, event)
                await self._generation.generate(context, event, normalized_event_id)
                await self._complete(db, consumer_id)
                return {"status": "processed", "generated": True}
            except Exception:
                await db.rollback()
                await db.execute(
                    update(github_raw_event_consumers)
                    .where(github_raw_event_consumers.c.id == consumer_id)
                    .values(processing_state="failed", updated_at=func.now())
                )
                await db.commit()
                raise

    async def _claim(self, db: AsyncSession, consumer_id: UUID) -> GitHubWorkflowContext | None:
        state = await db.scalar(
            select(github_raw_event_consumers.c.processing_state).where(
                github_raw_event_consumers.c.id == consumer_id
            )
        )
        if state == "processed":
            return None
        claimed = await db.execute(
            update(github_raw_event_consumers)
            .where(
                github_raw_event_consumers.c.id == consumer_id,
                github_raw_event_consumers.c.processing_state.in_(
                    ["pending", "enqueued", "failed"]
                ),
            )
            .values(
                processing_state="enqueued",
                processing_started_at=func.now(),
                updated_at=func.now(),
            )
        )
        await db.commit()
        if cast(CursorResult[object], claimed).rowcount != 1:
            resolved = await db.scalar(
                select(github_raw_event_consumers.c.processing_state).where(
                    github_raw_event_consumers.c.id == consumer_id
                )
            )
            if resolved == "processed":
                return None
            raise RuntimeError("GitHub consumer is not available for processing.")
        row = (
            await db.execute(
                select(
                    github_raw_event_consumers.c.id.label("consumer_id"),
                    github_raw_event_consumers.c.raw_event_id,
                    github_raw_event_consumers.c.workspace_id,
                    github_raw_event_consumers.c.user_id,
                    github_raw_events.c.event_name,
                    github_raw_events.c.payload_ciphertext,
                    repos.c.id.label("repo_id"),
                    repos.c.repo_name,
                    repos.c.repo_full_name,
                    repos.c.private,
                    repos.c.description,
                    repos.c.tracked_branch,
                )
                .select_from(
                    github_raw_event_consumers.join(
                        github_raw_events,
                        github_raw_events.c.id == github_raw_event_consumers.c.raw_event_id,
                    ).join(
                        repos,
                        and_(
                            repos.c.id == github_raw_event_consumers.c.repo_id,
                            repos.c.workspace_id == github_raw_event_consumers.c.workspace_id,
                            repos.c.user_id == github_raw_event_consumers.c.user_id,
                        ),
                    )
                )
                .where(
                    github_raw_event_consumers.c.id == consumer_id,
                    repos.c.is_tracked.is_(True),
                    repos.c.repo_full_name == github_raw_events.c.repo_full_name,
                )
            )
        ).one_or_none()
        if row is None:
            raise RuntimeError("GitHub consumer repository is no longer eligible.")
        decoded = json.loads(
            decrypt_github_webhook_payload(row.payload_ciphertext, self._encryption_key)
        )
        if not isinstance(decoded, dict):
            raise ValueError("Stored GitHub payload must be an object.")
        return GitHubWorkflowContext(
            consumer_id=row.consumer_id,
            raw_event_id=row.raw_event_id,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            repo_id=row.repo_id,
            repo_name=row.repo_name,
            repo_full_name=row.repo_full_name,
            repo_private=row.private,
            repo_description=row.description,
            tracked_branch=row.tracked_branch,
            event_name=row.event_name,
            payload=decoded,
        )

    async def _persist_normalized(
        self, db: AsyncSession, context: GitHubWorkflowContext, event: NormalizedEvent
    ) -> UUID:
        normalized_id = uuid5(context.workspace_id, event.dedupe_key)
        values = {
            "id": normalized_id,
            "workspace_id": context.workspace_id,
            "user_id": context.user_id,
            "raw_event_id": context.raw_event_id,
            "repo_id": context.repo_id,
            "event_type": event.event_type,
            "repo_full_name": event.repo_full_name,
            "title": event.title,
            "description": event.description,
            "url": event.url,
            "occurred_at": datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00")),
            "sha": event.sha,
            "dedupe_key": event.dedupe_key,
            "enrichment": (
                event.push_context.model_dump(mode="json") if event.push_context else None
            ),
            "branch_name": event.branch_name,
            "touched_paths": event.touched_paths,
        }
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            statement: Any = postgresql_insert(normalized_events)
        elif dialect == "sqlite":
            statement = sqlite_insert(normalized_events)
        else:
            raise RuntimeError("Workflow persistence requires PostgreSQL or SQLite.")
        await db.execute(
            statement.values(**values).on_conflict_do_nothing(
                index_elements=["workspace_id", "dedupe_key"]
            )
        )
        await db.commit()
        stored_id = await db.scalar(
            select(normalized_events.c.id).where(
                normalized_events.c.workspace_id == context.workspace_id,
                normalized_events.c.dedupe_key == event.dedupe_key,
            )
        )
        if stored_id is None:
            raise RuntimeError("Normalized event persistence failed.")
        return cast(UUID, stored_id)

    @staticmethod
    async def _complete(db: AsyncSession, consumer_id: UUID) -> None:
        await db.execute(
            update(github_raw_event_consumers)
            .where(github_raw_event_consumers.c.id == consumer_id)
            .values(
                processing_state="processed",
                processed_at=func.now(),
                updated_at=func.now(),
            )
        )
        await db.commit()
