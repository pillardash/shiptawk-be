from decimal import Decimal
from typing import cast
from uuid import UUID

from app.modules.drafts.policies.angles import AngleSelector
from app.modules.drafts.policies.evidence import CandidateEvidenceValidator
from app.modules.drafts.policies.public_safety import PublicSafeSummaryEngine
from app.modules.drafts.policies.ranking import TweetOptionRanker
from app.modules.drafts.policies.scoring import CandidateScoringEngine
from app.modules.drafts.prompts.tweet_candidates_prompt import (
    PROMPT_VERSION,
    ROUTE_NAME,
    build_tweet_candidates_envelope,
)
from app.modules.drafts.repositories.tweet_candidate_repository import TweetCandidateRepository
from app.modules.drafts.schemas.evidence import GeneratedVariant, PublicSafeSummary
from app.modules.drafts.schemas.orchestration import GenerationExecutionResult, GenerationInput
from app.modules.drafts.schemas.provider import StructuredGenerationOutput
from app.modules.drafts.schemas.ranking import TweetOption
from app.modules.llm.providers.generation_result import LLMGenerationTelemetry
from app.modules.llm.providers.llm_exceptions import (
    LLMInvalidResponseError,
    LLMPermanentError,
    LLMTransientError,
)
from app.modules.llm.repositories.generation_run_repository import GenerationRunRepository
from app.modules.llm.services.llm_execution_service import (
    LLMExecutionInProgressError,
    LLMExecutionService,
    LLMIdempotencyConflictError,
    LLMLeaseLostError,
)


class GenerationFailure(Exception):
    """Controlled generation failure without provider content."""


class TransientGenerationFailure(GenerationFailure):
    pass


class InvalidGenerationOutput(GenerationFailure):
    pass


class TweetGenerationService:
    def __init__(
        self,
        *,
        runs: GenerationRunRepository,
        candidates: TweetCandidateRepository,
        llm: LLMExecutionService,
    ) -> None:
        self._runs = runs
        self._candidates = candidates
        self._llm = llm

    async def run(self, generation_input: GenerationInput) -> GenerationExecutionResult:
        run_id = self._runs.run_id(generation_input.workspace_id, generation_input.stable_key)
        existing = await self._runs.get(generation_input.workspace_id, run_id)
        if existing is not None:
            candidates = [
                TweetOption.model_validate(candidate)
                for candidate in existing.get("candidate_snapshot", [])
            ]
            return GenerationExecutionResult(
                outcome="completed" if existing["status"] == "completed" else "failed",
                run_id=run_id,
                reused=True,
                candidates=candidates,
            )

        score = CandidateScoringEngine().score(
            event=generation_input.event,
            enrichment=generation_input.enrichment,
            repo_private=generation_input.repo_private,
            generation_context=generation_input.context,
            drafts_created_this_week=generation_input.drafts_created_this_week,
        )
        if generation_input.trigger == "event_pipeline" and score.outcome != "generate":
            return GenerationExecutionResult(
                outcome=score.outcome, run_id=None, reused=False, candidates=[]
            )
        if generation_input.product_id is None:
            raise InvalidGenerationOutput("generation_product_context_required")

        summary = PublicSafeSummaryEngine().build(
            event=generation_input.event,
            generation_context=generation_input.context,
            enrichment=generation_input.enrichment,
        )
        angles = AngleSelector().select(generation_input.event, summary)
        selected_angle = generation_input.requested_angle or angles.best
        envelope = build_tweet_candidates_envelope(
            generation_input.context,
            summary,
            selected_angle,
            generation_input.correlation_id,
        )

        def validate(value: object) -> dict[str, object]:
            return self._validate_and_rank(value, generation_input, summary)

        try:
            persisted = await self._llm.execute(
                workspace_id=generation_input.workspace_id,
                product_id=generation_input.product_id,
                use_case=generation_input.trigger,
                idempotency_key=generation_input.stable_key,
                route_name=ROUTE_NAME,
                envelope=envelope,
                validate_output=validate,
                sanitized_metadata={"promptVersion": PROMPT_VERSION},
                lease_owner=f"draft-generation:{generation_input.correlation_id}",
            )
        except LLMTransientError as exc:
            raise TransientGenerationFailure("transient_provider_failure") from exc
        except (LLMExecutionInProgressError, LLMLeaseLostError) as exc:
            raise TransientGenerationFailure("generation_in_progress") from exc
        except (
            LLMIdempotencyConflictError,
            LLMInvalidResponseError,
            LLMPermanentError,
        ) as exc:
            raise InvalidGenerationOutput("invalid_structured_output") from exc

        candidates = [
            TweetOption.model_validate(candidate)
            for candidate in cast(list[object], persisted.validated_output["candidates"])
        ]
        telemetry = persisted.result.telemetry if persisted.result is not None else None
        context_snapshot: dict[str, object] = {
            "idempotencyKey": generation_input.stable_key,
            "eventDedupeKey": generation_input.event.dedupe_key,
            "score": score.score,
            "selectedAngle": selected_angle,
            "alternateAngles": angles.alternates,
        }
        candidate_snapshot = [item.model_dump(mode="json") for item in candidates]
        inserted = await self._runs.save_completed(
            run_id=run_id,
            workspace_id=generation_input.workspace_id,
            user_id=generation_input.user_id,
            repo_id=generation_input.repo_id,
            product_id=generation_input.product_id,
            normalized_event_id=generation_input.normalized_event_id,
            context_snapshot=context_snapshot,
            provider=telemetry.provider if telemetry else "llm-ledger-replay",
            model=telemetry.model if telemetry else "validated-snapshot",
            duration_ms=telemetry.latency_ms if telemetry else None,
            provider_parameters=self._metadata(
                generation_input.correlation_id, persisted.execution_id, telemetry
            ),
            candidate_snapshot=candidate_snapshot,
            trigger=generation_input.trigger,
            requested_angle=generation_input.requested_angle,
        )
        if not inserted:
            canonical = await self._runs.get(generation_input.workspace_id, run_id)
            if canonical is None:
                raise TransientGenerationFailure("generation_run_race")
            return GenerationExecutionResult(
                outcome="completed",
                run_id=run_id,
                reused=True,
                candidates=[
                    TweetOption.model_validate(item)
                    for item in canonical.get("candidate_snapshot", [])
                ],
            )
        await self._candidates.save_many(
            run_id=run_id,
            workspace_id=generation_input.workspace_id,
            user_id=generation_input.user_id,
            repo_id=generation_input.repo_id,
            normalized_event_id=generation_input.normalized_event_id,
            candidates=candidates,
        )
        return GenerationExecutionResult(
            outcome="completed",
            run_id=run_id,
            reused=persisted.replayed,
            candidates=candidates,
        )

    @staticmethod
    def _validate_and_rank(
        value: object,
        generation_input: GenerationInput,
        summary: PublicSafeSummary,
    ) -> dict[str, object]:
        structured = StructuredGenerationOutput.model_validate(value)
        variants = [
            GeneratedVariant(
                content=candidate.content,
                variant=candidate.variant,
                angle=candidate.angle,
                rationale=candidate.rationale,
                evidence_ids=candidate.evidence_ids,
            )
            for candidate in structured.candidates
        ]
        validated = CandidateEvidenceValidator().validate(
            variants=variants,
            summary=summary,
            product_name=generation_input.context.product_name,
            subject_name=generation_input.context.subject_name,
            repo_name=generation_input.event.repo_full_name,
        )
        if not validated.accepted:
            raise ValueError("no_evidence_backed_candidates")
        ranked = TweetOptionRanker().rank(
            variants=validated.accepted,
            event=generation_input.event,
            summary=summary,
            content_memory=generation_input.content_memory,
        )
        return {"candidates": [item.model_dump(mode="json") for item in ranked]}

    @staticmethod
    def _metadata(
        correlation_id: str,
        execution_id: UUID,
        telemetry: LLMGenerationTelemetry | None,
    ) -> dict[str, object]:
        usage = telemetry.usage.model_dump(mode="json", by_alias=True) if telemetry else {}
        cost = telemetry.estimated_cost if telemetry else None
        return {
            "requestParameters": {"temperature": 0},
            "promptVersion": PROMPT_VERSION,
            "correlationId": telemetry.correlation_id if telemetry else correlation_id,
            "llmExecutionId": str(execution_id),
            "usage": usage,
            "costEstimateUsd": float(cost or Decimal(0)),
            "failureCategory": None,
        }
