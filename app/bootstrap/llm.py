from dataclasses import dataclass

from app.bootstrap.clients import SharedHTTPClients
from app.core.config import Settings
from app.db.session import get_sessionmaker
from app.modules.llm.policies.llm_route_policy import (
    LLMModelRegistration,
    LLMRoutePlan,
    LLMRoutePolicy,
    LLMRouteStep,
    ModelPricing,
)
from app.modules.llm.providers.openai.openai_adapter import OpenAIAdapter
from app.modules.llm.providers.openai.openai_client import OpenAIClient
from app.modules.llm.providers.provider_registry import LLMProviderRegistry
from app.modules.llm.services.llm_execution_service import LLMExecutionService


@dataclass(frozen=True, slots=True)
class LLMResources:
    providers: LLMProviderRegistry
    routes: LLMRoutePolicy
    execution_service: LLMExecutionService


def create_llm(settings: Settings, clients: SharedHTTPClients) -> LLMResources:
    providers = {}
    if settings.generation_provider == "openai":
        assert settings.openai_api_key is not None
        providers["openai"] = OpenAIAdapter(
            OpenAIClient(
                api_key=settings.openai_api_key.get_secret_value(),
                timeout_seconds=settings.generation_timeout_seconds,
                http_client=clients.openai,
            )
        )
    registration = LLMModelRegistration(
        name="structured-generation-openai",
        provider="openai",
        model=settings.openai_model,
        capabilities={"structured_output"},
        pricing=ModelPricing(
            input_per_million=settings.openai_input_price_per_million,
            output_per_million=settings.openai_output_price_per_million,
        ),
        pricing_version=settings.openai_pricing_version,
    )
    routes = LLMRoutePolicy(
        {registration.name: registration},
        {
            "product-context": LLMRoutePlan(
                name="product-context",
                steps=(LLMRouteStep(model=registration.name),),
                max_attempts=1,
            ),
            "tweet-candidates": LLMRoutePlan(
                name="tweet-candidates",
                steps=(LLMRouteStep(model=registration.name),),
                max_attempts=1,
            ),
            **{
                name: LLMRoutePlan(
                    name=name,
                    steps=(LLMRouteStep(model=registration.name),),
                    max_attempts=1,
                )
                for name in (
                    "operator-recommendation",
                    "operator-asset-seo",
                    "operator-asset-blog",
                    "operator-asset-product-update",
                    "operator-asset-x",
                    "operator-risk-review",
                )
            },
        },
    )
    registry = LLMProviderRegistry(providers)
    return LLMResources(
        providers=registry,
        routes=routes,
        execution_service=LLMExecutionService(get_sessionmaker(), registry, routes),
    )
