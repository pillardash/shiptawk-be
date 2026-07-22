import json
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.generation.models import GenerationRequest, GenerationTelemetry
from app.domains.generation.provider import GenerationProvider, ProviderTransientError
from app.domains.generation.repository import GenerationRepository
from app.domains.legacy.models import product_repositories, repos
from app.domains.products.models import Product
from app.domains.products.repository import get_product_for_workspace
from app.domains.products.schemas import (
    ProductContextGeneratedOutput,
    ProductContextGenerationRequest,
    ProductContextGenerationResponse,
    ProductContextPrefillDraft,
)
from app.shared.exceptions import BadRequestError, NotFoundError, ServiceUnavailableError

PROMPT_VERSION = "product-context.v1"


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


def _metadata(telemetry: GenerationTelemetry) -> dict[str, object]:
    return {
        "requestParameters": {"temperature": 0},
        "promptVersion": PROMPT_VERSION,
        "correlationId": telemetry.correlation_id,
        "usage": {
            "inputTokens": telemetry.input_tokens,
            "outputTokens": telemetry.output_tokens,
            "totalTokens": telemetry.total_tokens,
        },
        "costEstimateUsd": telemetry.cost_estimate_usd,
        "failureCategory": None,
    }


async def generate_product_context(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    payload: ProductContextGenerationRequest,
    provider: GenerationProvider,
    model: str,
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

    repository = GenerationRepository(db)
    stable_key = f"product-context:{product_id}:{payload.idempotency_key}"
    run_id = repository.run_id(workspace_id, stable_key)
    existing = await repository.get_run(workspace_id, run_id)
    if existing is not None and existing["candidate_snapshot"]:
        output = ProductContextGeneratedOutput.model_validate(existing["candidate_snapshot"][0])
        return _response(
            product,
            output,
            website_url,
            source_note="Generated from product and grouped repository evidence.",
        )

    repo_rows = (
        await db.execute(
            select(
                repos.c.repo_name,
                repos.c.repo_full_name,
                repos.c.description,
                product_repositories.c.repo_role,
                product_repositories.c.role_description_override,
                product_repositories.c.generated_role_description,
            )
            .select_from(
                product_repositories.join(
                    repos,
                    (repos.c.id == product_repositories.c.repo_id)
                    & (repos.c.workspace_id == product_repositories.c.workspace_id),
                )
            )
            .where(
                product_repositories.c.workspace_id == workspace_id,
                product_repositories.c.product_id == product_id,
                repos.c.is_tracked.is_(True),
            )
            .order_by(product_repositories.c.is_primary_repo.desc(), repos.c.id.asc())
        )
    ).mappings()
    public_input = {
        "product": {
            "name": product.name,
            "description": product.description or "",
            "websiteUrl": website_url,
            "targetAudience": product.target_audience or "",
            "messagingAngle": product.messaging_angle or "",
            "primaryCustomerPain": product.primary_customer_pain or "",
            "desiredOutcome": product.desired_outcome or "",
            "positioningStatement": product.positioning_statement or "",
            "proofPoints": product.proof_points,
            "customerUseCases": product.customer_use_cases,
        },
        "repositories": [
            {
                "name": row["repo_name"],
                "fullName": row["repo_full_name"],
                "description": row["description"],
                "role": row["repo_role"],
                "roleDescription": row["role_description_override"]
                or row["generated_role_description"],
            }
            for row in repo_rows
        ],
    }
    request = GenerationRequest(
        model=model,
        prompt_version=PROMPT_VERSION,
        system_instructions=(
            "Return strict JSON product context grounded only in the supplied public product and "
            "repository evidence. Do not invent claims or private implementation details."
        ),
        input_text=json.dumps(public_input, separators=(",", ":")),
        parameters={"temperature": 0},
        correlation_id=stable_key,
    )
    try:
        result = await provider.generate(request)
    except ProviderTransientError as exc:
        raise ServiceUnavailableError(
            "Product context generation is temporarily unavailable.",
            code="generation_provider_transient_failure",
        ) from exc
    try:
        output = ProductContextGeneratedOutput.model_validate(result.output)
    except ValidationError as exc:
        raise BadRequestError(
            "Generation provider returned invalid product context.",
            code="invalid_generation_output",
        ) from exc
    serialized = output.model_dump(mode="json", by_alias=True)
    await repository.save_structured(
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=actor_id,
        product_id=product_id,
        context_snapshot={
            "idempotencyKey": payload.idempotency_key,
            "promptVersion": PROMPT_VERSION,
        },
        provider=result.telemetry.provider,
        model=result.telemetry.model,
        duration_ms=result.telemetry.latency_ms,
        provider_parameters=_metadata(result.telemetry),
        output=serialized,
        trigger="product_context",
    )
    return _response(
        product,
        output,
        website_url,
        source_note="Generated from product and grouped repository evidence.",
    )
