from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.generation.ranking import TweetOption
from app.domains.legacy.models import generation_runs, tweet_candidates


class GenerationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @staticmethod
    def run_id(workspace_id: UUID, stable_key: str) -> UUID:
        return uuid5(workspace_id, stable_key)

    async def get_run(self, workspace_id: UUID, run_id: UUID) -> dict[str, Any] | None:
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

    async def delete_retryable_failure(self, workspace_id: UUID, run_id: UUID) -> None:
        await self._db.execute(
            delete(generation_runs).where(
                generation_runs.c.workspace_id == workspace_id,
                generation_runs.c.id == run_id,
                generation_runs.c.status == "failed",
                generation_runs.c.failure_message == "transient_provider_failure",
            )
        )
        await self._commit()

    async def save_completed(
        self,
        *,
        run_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        repo_id: UUID,
        product_id: UUID | None,
        normalized_event_id: UUID,
        context_snapshot: dict[str, object],
        provider: str,
        model: str,
        duration_ms: int | None,
        provider_parameters: dict[str, object],
        candidates: list[TweetOption],
        trigger: str = "event_pipeline",
        requested_angle: str | None = None,
    ) -> None:
        candidate_rows: list[dict[str, object]] = []
        candidate_snapshot: list[dict[str, object]] = []
        for candidate in candidates:
            candidate_id = uuid5(run_id, f"{candidate.rank}:{candidate.content}")
            serialized = candidate.model_dump(mode="json")
            candidate_snapshot.append(serialized)
            candidate_rows.append(
                {
                    "id": candidate_id,
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "normalized_event_id": normalized_event_id,
                    "repo_id": repo_id,
                    "content": candidate.content,
                    "rank": candidate.rank,
                    "score": round(candidate.score),
                    "rationale": candidate.rationale,
                    "angle": candidate.angle,
                    "variant": candidate.variant,
                    "dimension_scores": candidate.dimension_scores.model_dump(mode="json"),
                    "generation_run_id": run_id,
                }
            )

        await self._db.execute(
            insert(generation_runs).values(
                id=run_id,
                workspace_id=workspace_id,
                user_id=user_id,
                repo_id=repo_id,
                product_id=product_id,
                normalized_event_id=normalized_event_id,
                trigger=trigger,
                requested_angle=requested_angle,
                context_version=1,
                context_snapshot=context_snapshot,
                provider=provider,
                model=model,
                system_prompt="[not persisted]",
                user_prompt="[not persisted]",
                status="completed",
                duration_ms=duration_ms,
                candidate_snapshot=candidate_snapshot,
                provider_parameters=provider_parameters,
            )
        )
        if candidate_rows:
            await self._db.execute(insert(tweet_candidates), candidate_rows)
        await self._commit()

    async def save_failed(
        self,
        *,
        run_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        repo_id: UUID,
        product_id: UUID | None,
        normalized_event_id: UUID,
        context_snapshot: dict[str, object],
        provider: str,
        model: str,
        failure_message: str,
        duration_ms: int | None,
        provider_parameters: dict[str, object],
        trigger: str = "event_pipeline",
        requested_angle: str | None = None,
    ) -> None:
        await self._db.execute(
            insert(generation_runs).values(
                id=run_id,
                workspace_id=workspace_id,
                user_id=user_id,
                repo_id=repo_id,
                product_id=product_id,
                normalized_event_id=normalized_event_id,
                trigger=trigger,
                requested_angle=requested_angle,
                context_version=1,
                context_snapshot=context_snapshot,
                provider=provider,
                model=model,
                system_prompt="[not persisted]",
                user_prompt="[not persisted]",
                status="failed",
                failure_message=failure_message,
                duration_ms=duration_ms,
                provider_parameters=provider_parameters,
            )
        )
        await self._commit()

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
    ) -> None:
        await self._db.execute(
            insert(generation_runs).values(
                id=run_id,
                workspace_id=workspace_id,
                user_id=user_id,
                repo_id=None,
                product_id=product_id,
                normalized_event_id=None,
                trigger=trigger,
                context_version=1,
                context_snapshot=context_snapshot,
                provider=provider,
                model=model,
                system_prompt="[not persisted]",
                user_prompt="[not persisted]",
                status="completed",
                duration_ms=duration_ms,
                candidate_snapshot=[output],
                provider_parameters=provider_parameters,
            )
        )
        await self._commit()

    async def _commit(self) -> None:
        try:
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise
