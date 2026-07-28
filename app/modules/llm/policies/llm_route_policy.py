from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelPricing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    cache_read_per_million: Decimal = Field(default=Decimal(0), ge=0)
    cache_write_per_million: Decimal = Field(default=Decimal(0), ge=0)


class LLMModelRegistration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    capabilities: frozenset[str]
    pricing: ModelPricing
    pricing_version: str = Field(min_length=1)


class LLMRouteStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(min_length=1)


class LLMRoutePlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    steps: tuple[LLMRouteStep, ...] = Field(min_length=1)
    max_attempts: int = Field(ge=1)

    @model_validator(mode="after")
    def bounded_attempts(self) -> "LLMRoutePlan":
        if self.max_attempts > len(self.steps):
            raise ValueError("max_attempts cannot exceed route steps")
        return self


class ResolvedLLMRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    registrations: tuple[LLMModelRegistration, ...]


class LLMRoutePolicy:
    """Pure, explicit mapping from a named route to ordered model registrations."""

    def __init__(
        self,
        registrations: dict[str, LLMModelRegistration],
        plans: dict[str, LLMRoutePlan],
    ) -> None:
        self._registrations = dict(registrations)
        self._plans = dict(plans)

    def resolve(self, name: str, *, required_capabilities: set[str]) -> ResolvedLLMRoute:
        try:
            plan = self._plans[name]
            registrations = tuple(
                self._registrations[step.model] for step in plan.steps[: plan.max_attempts]
            )
        except KeyError as exc:
            raise ValueError(f"unknown_llm_route_or_model:{exc.args[0]}") from exc
        for registration in registrations:
            missing = required_capabilities - registration.capabilities
            if missing:
                raise ValueError(f"llm_model_missing_capabilities:{registration.name}")
        return ResolvedLLMRoute(name=name, registrations=registrations)


def estimate_cost(
    registration: LLMModelRegistration,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> Decimal:
    pricing = registration.pricing
    total = (
        Decimal(input_tokens) * pricing.input_per_million
        + Decimal(output_tokens) * pricing.output_per_million
        + Decimal(cache_read_tokens) * pricing.cache_read_per_million
        + Decimal(cache_write_tokens) * pricing.cache_write_per_million
    ) / Decimal(1_000_000)
    return total.quantize(Decimal("0.000000000001"))
