from uuid import UUID, uuid5

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.drafts.models import tweet_candidates
from app.modules.drafts.schemas.ranking import TweetOption


class TweetCandidateRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def save_many(
        self,
        *,
        run_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        repo_id: UUID,
        normalized_event_id: UUID,
        candidates: list[TweetOption],
    ) -> None:
        rows = [
            {
                "id": uuid5(run_id, f"{candidate.rank}:{candidate.content}"),
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
            for candidate in candidates
        ]
        if rows:
            await self._db.execute(insert(tweet_candidates), rows)
