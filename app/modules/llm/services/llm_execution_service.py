import asyncio
import contextlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.llm.models.llm_execution import LLMExecution
from app.modules.llm.models.llm_execution_attempt import LLMExecutionAttempt
from app.modules.llm.policies.llm_fingerprint_policy import (
    canonical_request_fingerprint,
    result_fingerprint,
)
from app.modules.llm.policies.llm_route_policy import LLMRoutePolicy, estimate_cost
from app.modules.llm.providers.generation_result import LLMGenerationResult
from app.modules.llm.providers.llm_exceptions import (
    LLMInvalidResponseError,
    LLMProviderError,
    LLMTransientError,
)
from app.modules.llm.providers.prompt_envelope import PromptEnvelope
from app.modules.llm.providers.provider_registry import LLMProviderRegistry
from app.modules.llm.repositories import llm_execution_repository as repository


class LLMIdempotencyConflictError(Exception):
    pass


class LLMExecutionInProgressError(Exception):
    pass


class LLMLeaseLostError(Exception):
    pass


class LLMOutputValidationError(LLMInvalidResponseError):
    pass


MAX_VALIDATED_OUTPUT_BYTES = 64 * 1024


class PersistedLLMExecutionResult:
    def __init__(
        self,
        execution_id: UUID,
        result: LLMGenerationResult | None,
        validated_output: dict[str, object],
        *,
        replayed: bool,
    ) -> None:
        self.execution_id = execution_id
        self.result = result
        self.validated_output = validated_output
        self.replayed = replayed


class LLMExecutionService:
    """Runs named routes with explicit fallback and short database transactions."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: LLMProviderRegistry,
        routes: LLMRoutePolicy,
        *,
        lease_duration: timedelta = timedelta(minutes=2),
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._routes = routes
        self._lease_duration = lease_duration
        if lease_duration.total_seconds() <= 0:
            raise ValueError("lease_duration must be positive")

    async def execute(
        self,
        *,
        workspace_id: UUID,
        product_id: UUID,
        use_case: str,
        idempotency_key: str,
        route_name: str,
        envelope: PromptEnvelope,
        validate_output: Callable[[object], dict[str, object]],
        sanitized_metadata: Mapping[str, object] | None = None,
        lease_owner: str,
    ) -> PersistedLLMExecutionResult:
        route = self._routes.resolve(route_name, required_capabilities={"structured_output"})
        fingerprint = canonical_request_fingerprint(envelope, route_name)
        route_snapshot: dict[str, object] = {
            "route": route.name,
            "models": [
                {"registration": item.name, "provider": item.provider, "model": item.model}
                for item in route.registrations
            ],
        }
        execution = await self._create_or_replay(
            workspace_id,
            product_id,
            use_case,
            idempotency_key,
            fingerprint,
            route_name,
            route_snapshot,
            dict(sanitized_metadata or {}),
        )
        if execution.status == "completed":
            if execution.validated_output_snapshot is None:
                raise LLMOutputValidationError("llm_validated_snapshot_missing")
            return PersistedLLMExecutionResult(
                execution.id,
                None,
                execution.validated_output_snapshot,
                replayed=True,
            )
        if execution.status == "failed":
            async with self._sessions() as db:
                reopened = await repository.reopen_transient_failure(
                    db,
                    execution.id,
                    execution.workspace_id,
                    execution.product_id,
                )
                if reopened:
                    await db.commit()
                else:
                    await db.rollback()
                    raise LLMInvalidResponseError("llm_execution_failed_non_retryable")

        token = uuid4()
        now = datetime.now(UTC)
        async with self._sessions() as db:
            won = await repository.acquire_lease(
                db,
                execution.id,
                workspace_id,
                product_id,
                owner=lease_owner,
                token=token,
                now=now,
                expires_at=now + self._lease_duration,
            )
            await db.commit()
        if not won:
            raise LLMExecutionInProgressError("llm_execution_lease_unavailable")

        last_error: LLMProviderError | None = None
        for registration in route.registrations:
            request = envelope.model_copy(update={"model": registration.model})
            async with self._sessions() as db:
                attempt_number = await repository.allocate_attempt(
                    db, execution.id, workspace_id, product_id, token
                )
                if attempt_number is None:
                    await db.rollback()
                    raise LLMLeaseLostError("llm_execution_lease_lost")
                await db.commit()

            lease_lost = asyncio.Event()
            heartbeat = asyncio.create_task(
                self._heartbeat(execution, token, lease_lost),
                name=f"llm-lease-heartbeat:{execution.id}",
            )
            try:
                generated = await self._providers.get(registration.provider).generate(request)
            except LLMProviderError as exc:
                last_error = exc
                await self._record_attempt(
                    execution,
                    token,
                    attempt_number,
                    registration.provider,
                    registration.model,
                    registration.pricing_version,
                    status="failed",
                    error_category=type(exc).__name__,
                )
                if isinstance(exc, LLMTransientError) and registration != route.registrations[-1]:
                    continue
                await self._finish_failed(execution, token, type(exc).__name__)
                raise
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat

            if lease_lost.is_set():
                raise LLMLeaseLostError("llm_execution_lease_lost")

            usage = generated.telemetry.usage
            if generated.telemetry.refusal or generated.output is None:
                refusal_error = LLMInvalidResponseError("llm_refusal_or_empty_output")
                await self._record_attempt(
                    execution,
                    token,
                    attempt_number,
                    registration.provider,
                    registration.model,
                    registration.pricing_version,
                    status="failed",
                    result=generated,
                    error_category=type(refusal_error).__name__,
                )
                await self._finish_failed(execution, token, type(refusal_error).__name__)
                raise refusal_error
            try:
                validated_output = validate_output(generated.output)
                encoded_output = json.dumps(
                    validated_output, sort_keys=True, separators=(",", ":")
                ).encode()
                if len(encoded_output) > MAX_VALIDATED_OUTPUT_BYTES:
                    raise ValueError("validated output exceeds retention limit")
            except (TypeError, ValueError) as exc:
                validation_error = LLMOutputValidationError("llm_output_validation_failed")
                await self._record_attempt(
                    execution,
                    token,
                    attempt_number,
                    registration.provider,
                    registration.model,
                    registration.pricing_version,
                    status="failed",
                    error_category=type(validation_error).__name__,
                )
                await self._finish_failed(execution, token, type(validation_error).__name__)
                raise validation_error from exc
            cost = estimate_cost(
                registration,
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )
            generated = generated.model_copy(
                update={
                    "telemetry": generated.telemetry.model_copy(
                        update={
                            "estimated_cost": cost,
                            "pricing_version": registration.pricing_version,
                        }
                    )
                }
            )
            await self._record_attempt(
                execution,
                token,
                attempt_number,
                registration.provider,
                registration.model,
                registration.pricing_version,
                status="completed",
                result=generated,
            )
            async with self._sessions() as db:
                updated = await repository.fence_update(
                    db,
                    execution.id,
                    execution.workspace_id,
                    execution.product_id,
                    token,
                    status="completed",
                    result_fingerprint=result_fingerprint(validated_output),
                    validated_output_snapshot=validated_output,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                )
                if not updated:
                    await db.rollback()
                    raise LLMLeaseLostError("llm_execution_lease_lost")
                await db.commit()
            return PersistedLLMExecutionResult(
                execution.id, generated, validated_output, replayed=False
            )
        assert last_error is not None
        raise last_error

    async def _heartbeat(
        self, execution: LLMExecution, token: UUID, lease_lost: asyncio.Event
    ) -> None:
        interval = self._lease_duration.total_seconds() / 3
        while True:
            await asyncio.sleep(interval)
            async with self._sessions() as db:
                renewed = await repository.renew_lease(
                    db,
                    execution.id,
                    execution.workspace_id,
                    execution.product_id,
                    token,
                    expires_at=datetime.now(UTC) + self._lease_duration,
                )
                if not renewed:
                    await db.rollback()
                    lease_lost.set()
                    return
                await db.commit()

    async def _create_or_replay(
        self,
        workspace_id: UUID,
        product_id: UUID,
        use_case: str,
        idempotency_key: str,
        fingerprint: str,
        route_name: str,
        route_snapshot: dict[str, object],
        metadata: dict[str, object],
    ) -> LLMExecution:
        async with self._sessions() as db:
            execution = LLMExecution(
                workspace_id=workspace_id,
                product_id=product_id,
                use_case=use_case,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                route_name=route_name,
                route_snapshot=route_snapshot,
                sanitized_metadata=metadata,
                status="pending",
            )
            try:
                await repository.flush_execution(db, execution)
                await db.commit()
            except IntegrityError:
                await db.rollback()
                existing = await repository.get_by_key(
                    db, workspace_id, product_id, use_case, idempotency_key
                )
                if existing is None:
                    raise
                execution = existing
            if execution.request_fingerprint != fingerprint:
                raise LLMIdempotencyConflictError("llm_idempotency_fingerprint_conflict")
            return execution

    async def _record_attempt(
        self,
        execution: LLMExecution,
        token: UUID,
        number: int,
        provider: str,
        model: str,
        pricing_version: str,
        *,
        status: str,
        result: LLMGenerationResult | None = None,
        error_category: str | None = None,
    ) -> None:
        telemetry = result.telemetry if result else None
        async with self._sessions() as db:
            await repository.flush_attempt(
                db,
                LLMExecutionAttempt(
                    workspace_id=execution.workspace_id,
                    product_id=execution.product_id,
                    execution_id=execution.id,
                    attempt_number=number,
                    lease_token=token,
                    provider=provider,
                    model=model,
                    status=status,
                    provider_request_id=telemetry.provider_request_id if telemetry else None,
                    finish_reason=telemetry.finish_reason if telemetry else None,
                    refusal=telemetry.refusal if telemetry else False,
                    usage=telemetry.usage.model_dump(mode="json") if telemetry else {},
                    estimated_cost=telemetry.estimated_cost if telemetry else None,
                    pricing_version=pricing_version,
                    latency_ms=telemetry.latency_ms if telemetry else None,
                    error_category=error_category,
                ),
            )
            await db.commit()

    async def _finish_failed(self, execution: LLMExecution, token: UUID, category: str) -> None:
        async with self._sessions() as db:
            updated = await repository.fence_update(
                db,
                execution.id,
                execution.workspace_id,
                execution.product_id,
                token,
                status="failed",
                failure_category=category,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            if not updated:
                await db.rollback()
                raise LLMLeaseLostError("llm_execution_lease_lost")
            await db.commit()
