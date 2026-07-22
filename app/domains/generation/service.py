import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domains.generation.angles import AngleSelector
from app.domains.generation.content_memory import ContentMemory
from app.domains.generation.context import EffectiveGenerationContext, GenerationContextBuilder
from app.domains.generation.evidence import (
    CandidateEvidenceValidator,
    GeneratedVariant,
    PublicSafeSummary,
)
from app.domains.generation.models import (
    GenerationRequest,
    GenerationTelemetry,
    StructuredGenerationOutput,
)
from app.domains.generation.provider import (
    GenerationProvider,
    ProviderPermanentError,
    ProviderTransientError,
)
from app.domains.generation.ranking import TweetOption, TweetOptionRanker
from app.domains.generation.repository import GenerationRepository
from app.domains.generation.scoring import CandidateScoringEngine, PipelineOutcome
from app.domains.generation.summary import CommitEnrichment, PublicSafeSummaryEngine
from app.domains.integrations.event_normalizer import NormalizedEvent

PROMPT_VERSION = "tweet-candidates.v1"


class GenerationFailure(Exception):
    """Base controlled generation failure; messages never contain provider content."""


class TransientGenerationFailure(GenerationFailure):
    """Retryable provider failure."""


class InvalidGenerationOutput(GenerationFailure):
    """Non-retryable malformed or unsupported provider output."""


class ServiceModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class GenerationInput(ServiceModel):
    workspace_id: UUID
    user_id: UUID
    repo_id: UUID
    product_id: UUID | None
    normalized_event_id: UUID
    stable_key: str
    correlation_id: str
    event: NormalizedEvent
    context: EffectiveGenerationContext
    repo_private: bool
    drafts_created_this_week: int
    enrichment: CommitEnrichment | None = None
    content_memory: ContentMemory | None = None
    trigger: Literal["event_pipeline", "angle_regeneration"] = "event_pipeline"
    requested_angle: str | None = None


class GenerationExecutionResult(ServiceModel):
    outcome: PipelineOutcome | Literal["completed", "failed"]
    run_id: UUID | None
    reused: bool
    candidates: list[TweetOption]


class GenerationOrchestrator:
    def __init__(
        self,
        *,
        repository: GenerationRepository,
        provider: GenerationProvider,
        model: str,
        parameters: dict[str, object] | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._model = model
        self._parameters = parameters or {"temperature": 0}

    async def run(self, generation_input: GenerationInput) -> GenerationExecutionResult:
        run_id = self._repository.run_id(generation_input.workspace_id, generation_input.stable_key)
        existing = await self._repository.get_run(generation_input.workspace_id, run_id)
        if (
            existing is not None
            and existing["status"] == "failed"
            and existing.get("failure_message") == "transient_provider_failure"
        ):
            await self._repository.delete_retryable_failure(generation_input.workspace_id, run_id)
            existing = None
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

        summary = PublicSafeSummaryEngine().build(
            event=generation_input.event,
            generation_context=generation_input.context,
            enrichment=generation_input.enrichment,
        )
        angles = AngleSelector().select(generation_input.event, summary)
        selected_angle = generation_input.requested_angle or angles.best
        request = self._request(generation_input, summary, selected_angle)
        context_snapshot: dict[str, object] = {
            "idempotencyKey": generation_input.stable_key,
            "eventDedupeKey": generation_input.event.dedupe_key,
            "score": score.score,
            "selectedAngle": selected_angle,
            "alternateAngles": angles.alternates,
        }

        try:
            provider_result = await self._provider.generate(request)
        except ProviderTransientError as exc:
            await self._repository.save_failed(
                run_id=run_id,
                workspace_id=generation_input.workspace_id,
                user_id=generation_input.user_id,
                repo_id=generation_input.repo_id,
                product_id=generation_input.product_id,
                normalized_event_id=generation_input.normalized_event_id,
                context_snapshot=context_snapshot,
                provider=self._provider.name,
                model=self._model,
                failure_message="transient_provider_failure",
                duration_ms=None,
                provider_parameters=self._metadata(
                    generation_input.correlation_id, "transient_provider", None
                ),
                trigger=generation_input.trigger,
                requested_angle=generation_input.requested_angle,
            )
            raise TransientGenerationFailure("transient_provider_failure") from exc
        except ProviderPermanentError as exc:
            await self._repository.save_failed(
                run_id=run_id,
                workspace_id=generation_input.workspace_id,
                user_id=generation_input.user_id,
                repo_id=generation_input.repo_id,
                product_id=generation_input.product_id,
                normalized_event_id=generation_input.normalized_event_id,
                context_snapshot=context_snapshot,
                provider=self._provider.name,
                model=self._model,
                failure_message="invalid_provider_response",
                duration_ms=None,
                provider_parameters=self._metadata(
                    generation_input.correlation_id, "provider_error", None
                ),
                trigger=generation_input.trigger,
                requested_angle=generation_input.requested_angle,
            )
            raise InvalidGenerationOutput("invalid_provider_response") from exc

        try:
            structured = StructuredGenerationOutput.model_validate(provider_result.output)
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
                raise ValueError("No evidence-backed candidates")
        except (ValueError, TypeError) as exc:
            await self._persist_invalid_output(
                generation_input, run_id, context_snapshot, provider_result.telemetry
            )
            raise InvalidGenerationOutput("invalid_structured_output") from exc

        candidates = TweetOptionRanker().rank(
            variants=validated.accepted,
            event=generation_input.event,
            summary=summary,
            content_memory=generation_input.content_memory,
        )
        telemetry = provider_result.telemetry
        await self._repository.save_completed(
            run_id=run_id,
            workspace_id=generation_input.workspace_id,
            user_id=generation_input.user_id,
            repo_id=generation_input.repo_id,
            product_id=generation_input.product_id,
            normalized_event_id=generation_input.normalized_event_id,
            context_snapshot=context_snapshot,
            provider=telemetry.provider,
            model=telemetry.model,
            duration_ms=telemetry.latency_ms,
            provider_parameters=self._metadata(generation_input.correlation_id, None, telemetry),
            candidates=candidates,
            trigger=generation_input.trigger,
            requested_angle=generation_input.requested_angle,
        )
        return GenerationExecutionResult(
            outcome="completed", run_id=run_id, reused=False, candidates=candidates
        )

    def _request(
        self,
        generation_input: GenerationInput,
        summary: PublicSafeSummary,
        selected_angle: str,
    ) -> GenerationRequest:
        safe_summary = summary.model_dump(mode="json", exclude={"internal_summary"})
        safe_input = {
            "context": GenerationContextBuilder().compile_concise(generation_input.context),
            "publicEvidence": safe_summary,
            "selectedAngle": selected_angle,
        }
        return GenerationRequest(
            model=self._model,
            prompt_version=PROMPT_VERSION,
            system_instructions=(
                "Return JSON candidates grounded only in the supplied public evidence. "
                "Every candidate must cite evidenceIds."
            ),
            input_text=json.dumps(safe_input, separators=(",", ":")),
            parameters=self._parameters,
            correlation_id=generation_input.correlation_id,
        )

    async def _persist_invalid_output(
        self,
        generation_input: GenerationInput,
        run_id: UUID,
        context_snapshot: dict[str, object],
        telemetry: GenerationTelemetry,
    ) -> None:
        await self._repository.save_failed(
            run_id=run_id,
            workspace_id=generation_input.workspace_id,
            user_id=generation_input.user_id,
            repo_id=generation_input.repo_id,
            product_id=generation_input.product_id,
            normalized_event_id=generation_input.normalized_event_id,
            context_snapshot=context_snapshot,
            provider=telemetry.provider,
            model=telemetry.model,
            failure_message="invalid_structured_output",
            duration_ms=telemetry.latency_ms,
            provider_parameters=self._metadata(
                generation_input.correlation_id, "invalid_output", telemetry
            ),
            trigger=generation_input.trigger,
            requested_angle=generation_input.requested_angle,
        )

    def _metadata(
        self,
        correlation_id: str,
        failure_category: str | None,
        telemetry: GenerationTelemetry | None,
    ) -> dict[str, object]:
        usage: dict[str, object] = {}
        if telemetry is not None:
            usage = {
                "inputTokens": telemetry.input_tokens,
                "outputTokens": telemetry.output_tokens,
                "totalTokens": telemetry.total_tokens,
            }
        return {
            "requestParameters": self._parameters,
            "promptVersion": PROMPT_VERSION,
            "correlationId": (
                telemetry.correlation_id
                if telemetry is not None and telemetry.correlation_id
                else correlation_id
            ),
            "usage": usage,
            "costEstimateUsd": telemetry.cost_estimate_usd if telemetry is not None else None,
            "failureCategory": failure_category,
        }
