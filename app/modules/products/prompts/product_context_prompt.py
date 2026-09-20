import json

from app.modules.llm.providers.prompt_envelope import PromptEnvelope, PromptMessage
from app.modules.products.models import Product
from app.modules.products.schemas.product_schema import ProductContextGeneratedOutput
from app.modules.repos.schemas.product_repository_evidence_schema import ProductRepositoryEvidence

PROMPT_VERSION = "product-context.v2"
ROUTE_NAME = "product-context"


def build_product_context_envelope(
    product: Product,
    repositories: list[ProductRepositoryEvidence],
    website_url: str,
    correlation_id: str,
) -> PromptEnvelope:
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
        "repositories": [item.model_dump(mode="json", by_alias=True) for item in repositories],
    }
    return PromptEnvelope(
        model="route-owned",
        prompt_id="product-context",
        prompt_version=PROMPT_VERSION,
        input_schema_version="v1",
        output_schema_version="v1",
        messages=(
            PromptMessage(
                role="system",
                content=(
                    "Return strict JSON product context grounded only in the supplied public "
                    "product and repository evidence. Do not invent claims or private "
                    "implementation details. Identify the product's market and state its main "
                    "customer value proposition in plain language. Refuse if the evidence cannot "
                    "be used safely."
                ),
            ),
            PromptMessage(role="user", content=json.dumps(public_input, separators=(",", ":"))),
        ),
        output_schema_name="product_context",
        output_json_schema=ProductContextGeneratedOutput.model_json_schema(by_alias=True),
        parameters={"temperature": 0},
        correlation_id=correlation_id,
    )
