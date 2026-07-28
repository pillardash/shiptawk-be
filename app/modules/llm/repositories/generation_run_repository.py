from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.llm.models import generation_runs


class GenerationRunRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @staticmethod
    def run_id(workspace_id: UUID, stable_key: str) -> UUID:
        return uuid5(workspace_id, stable_key)

    async def get(self, workspace_id: UUID, run_id: UUID) -> dict[str, Any] | None:
        row = (
            (
                await self._db.execute(
                    select(generation_runs).where(
                        generation_runs.c.workspace_id == workspace_id,
                        generation_runs.c.id == run_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    async def _insert_conflict_safe(self, values: dict[str, object]) -> bool:
        dialect = self._db.get_bind().dialect.name
        if dialect == "postgresql":
            result = await self._db.execute(
                postgresql_insert(generation_runs).values(**values).on_conflict_do_nothing()
            )
        elif dialect == "sqlite":
            result = await self._db.execute(
                sqlite_insert(generation_runs).values(**values).on_conflict_do_nothing()
            )
        else:
            result = await self._db.execute(insert(generation_runs).values(**values))
        return bool(getattr(result, "rowcount", 0))

    async def save_completed(
        self,
        *,
        run_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        repo_id: UUID,
        product_id: UUID,
        normalized_event_id: UUID,
        context_snapshot: dict[str, object],
        provider: str,
        model: str,
        duration_ms: int | None,
        provider_parameters: dict[str, object],
        candidate_snapshot: list[dict[str, object]],
        trigger: str,
        requested_angle: str | None,
    ) -> bool:
        return await self._insert_conflict_safe(
            {
                "id": run_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "repo_id": repo_id,
                "product_id": product_id,
                "normalized_event_id": normalized_event_id,
                "trigger": trigger,
                "requested_angle": requested_angle,
                "context_version": 1,
                "context_snapshot": context_snapshot,
                "provider": provider,
                "model": model,
                "system_prompt": "[not persisted]",
                "user_prompt": "[not persisted]",
                "status": "completed",
                "duration_ms": duration_ms,
                "candidate_snapshot": candidate_snapshot,
                "provider_parameters": provider_parameters,
            }
        )

    async def save_structured(
        self,
        *,
        run_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        product_id: UUID,
        context_snapshot: dict[str, object],
        provider: str,
        model: str,
        duration_ms: int | None,
        provider_parameters: dict[str, object],
        output: dict[str, object],
        trigger: str,
    ) -> bool:
        return await self._insert_conflict_safe(
            {
                "id": run_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "product_id": product_id,
                "trigger": trigger,
                "context_version": 1,
                "context_snapshot": context_snapshot,
                "provider": provider,
                "model": model,
                "system_prompt": "[not persisted]",
                "user_prompt": "[not persisted]",
                "status": "completed",
                "duration_ms": duration_ms,
                "candidate_snapshot": [output],
                "provider_parameters": provider_parameters,
            }
        )
