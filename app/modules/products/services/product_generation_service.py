from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.modules.products.models import Product
from app.modules.products.prompts.product_context_prompt import (
    PROMPT_VERSION,
    ROUTE_NAME,
    build_product_context_envelope,
)
from app.modules.products.repositories.product_repository import get_product_for_workspace
from app.modules.products.schemas.product_schema import (
    ProductContextGeneratedOutput,
    ProductContextGenerationRequest,
    ProductContextGenerationResponse,
    ProductContextPrefillDraft,
)
from app.modules.repos.repositories.product_repository_evidence_repository import (
    list_product_repository_evidence,
)
from app.shared.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)


def _lines(values: list[str]) -> str:
    return "\n".join(values)


def _response(
    product: Product,
    output: ProductContextGeneratedOutput,
    website_url: str,
    *,
    source_note: str,
) -> ProductContextGenerationResponse:
    return ProductContextGenerationResponse(
        draft=ProductContextPrefillDraft(
            name=output.name,
            description=output.description,
            website_url=website_url,
            target_audience=output.target_audience,
            messaging_angle=output.messaging_angle,
            tone_override=output.tone_override,
            blocked_terms=_lines(product.blocked_terms),
            blocked_topics=_lines(product.blocked_topics),
            safe_public_boundaries=_lines(
                product.safe_public_boundaries or output.safe_public_boundaries
            ),
            primary_customer_pain=output.primary_customer_pain,
            desired_outcome=output.desired_outcome,
            positioning_statement=output.positioning_statement,
            proof_points=_lines(output.proof_points),
            customer_use_cases=_lines(output.customer_use_cases),
            content_goal=output.content_goal,
            cta_preference=output.cta_preference,
            founder_story_angle=output.founder_story_angle,
        ),
        source_note=source_note,
        confidence=output.confidence,
        needs_review=output.confidence < 65,
    )


def _saved_output(product: Product) -> ProductContextGeneratedOutput:
    return ProductContextGeneratedOutput(
        name=product.name,
        description=product.description or f"{product.name} product context.",
        target_audience=product.target_audience or "Product users",
        messaging_angle=product.messaging_angle or "Share concrete product progress.",
        tone_override=product.tone_override or "",
        safe_public_boundaries=product.safe_public_boundaries,
        primary_customer_pain=product.primary_customer_pain or "",
        desired_outcome=product.desired_outcome or "",
        positioning_statement=product.positioning_statement or "",
        proof_points=product.proof_points,
        customer_use_cases=product.customer_use_cases,
        content_goal=product.content_goal,
        cta_preference=product.cta_preference or "",
        founder_story_angle=product.founder_story_angle or "",
        confidence=92,
    )


def _metadata(telemetry: LLMGenerationTelemetry, execution_id: UUID) -> dict[str, object]:
    return {
        "requestParameters": {"temperature": 0},
        "promptVersion": PROMPT_VERSION,
        "correlationId": telemetry.correlation_id,
        "llmExecutionId": str(execution_id),
        "usage": telemetry.usage.model_dump(mode="json", by_alias=True),
        "costEstimateUsd": float(telemetry.estimated_cost or 0),
        "failureCategory": None,
    }


async def generate_product_context(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    payload: ProductContextGenerationRequest,
    llm: LLMExecutionService,
) -> ProductContextGenerationResponse:
    product = await get_product_for_workspace(db, workspace_id, product_id, actor_id)
    if product is None:
        raise NotFoundError("Product not found.", code="product_not_found")
    website_url = payload.website_url or product.website_url or ""
    if (
        not payload.force
        and product.description
        and product.target_audience
        and product.messaging_angle
    ):
        return _response(
            product,
            _saved_output(product),
            website_url,
            source_note="Using your saved product context.",
        )

    repository = GenerationRunRepository(db)
    stable_key = f"product-context:{product_id}:{payload.idempotency_key}"
    run_id = repository.run_id(workspace_id, stable_key)
    existing = await repository.get(workspace_id, run_id)
    if existing is not None and existing["candidate_snapshot"]:
        output = ProductContextGeneratedOutput.model_validate(existing["candidate_snapshot"][0])
        return _response(
            product,
            output,
            website_url,
            source_note="Generated from product and grouped repository evidence.",
        )

    repo_evidence = await list_product_repository_evidence(db, workspace_id, product_id)
    envelope = build_product_context_envelope(product, repo_evidence, website_url, stable_key)
    try:
        persisted = await llm.execute(
            workspace_id=workspace_id,
            product_id=product_id,
            use_case="product_context",
            idempotency_key=payload.idempotency_key,
            route_name=ROUTE_NAME,
            envelope=envelope,
            validate_output=lambda value: ProductContextGeneratedOutput.model_validate(
                value
            ).model_dump(mode="json", by_alias=True),
            sanitized_metadata={"promptVersion": PROMPT_VERSION},
            lease_owner=f"product-api:{actor_id}",
        )
    except LLMTransientError as exc:
        raise ServiceUnavailableError(
            "Product context generation is temporarily unavailable.",
            code="generation_provider_transient_failure",
        ) from exc
    except (LLMExecutionInProgressError, LLMLeaseLostError) as exc:
        raise ConflictError(
            "Product context generation is already in progress.",
            code="generation_in_progress",
        ) from exc
    except LLMIdempotencyConflictError as exc:
        raise ConflictError(
            "The idempotency key was already used for different input.",
            code="generation_idempotency_conflict",
        ) from exc
    except (LLMInvalidResponseError, LLMPermanentError) as exc:
        raise BadRequestError(
            "Product context could not be generated.", code="invalid_generation_output"
        ) from exc

    try:
        output = ProductContextGeneratedOutput.model_validate(persisted.validated_output)
    except ValidationError as exc:
        raise BadRequestError(
            "Product context could not be generated.", code="invalid_generation_output"
        ) from exc

    telemetry = persisted.result.telemetry if persisted.result is not None else None
    await repository.save_structured(
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=actor_id,
        product_id=product_id,
        context_snapshot={
            "idempotencyKey": payload.idempotency_key,
            "promptVersion": PROMPT_VERSION,
        },
        provider=telemetry.provider if telemetry else "llm-ledger-replay",
        model=telemetry.model if telemetry else "validated-snapshot",
        duration_ms=telemetry.latency_ms if telemetry else None,
        provider_parameters=(
            _metadata(telemetry, persisted.execution_id)
            if telemetry
            else {
                "promptVersion": PROMPT_VERSION,
                "llmExecutionId": str(persisted.execution_id),
                "replayed": True,
            }
        ),
        output=output.model_dump(mode="json", by_alias=True),
        trigger="product_context",
    )
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return _response(
        product,
        output,
        website_url,
        source_note="Generated from product and grouped repository evidence.",
    )
