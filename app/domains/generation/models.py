from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.generation.angles import AngleType

CandidateVariant = Literal[
    "problem_solution",
    "customer_value",
    "founder_insight",
    "soft_launch_cta",
    "concise_shipping_update",
]


class GenerationModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class GenerationRequest(GenerationModel):
    model: str
    prompt_version: str
    system_instructions: str
    input_text: str
    parameters: dict[str, object] = Field(default_factory=dict)
    correlation_id: str


class GenerationTelemetry(GenerationModel):
    provider: str
    model: str
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost_estimate_usd: float | None = Field(default=None, ge=0)
    correlation_id: str | None = None


class ProviderGenerationResult(GenerationModel):
    output: object
    telemetry: GenerationTelemetry


class StructuredGenerationCandidate(GenerationModel):
    content: str = Field(min_length=1, max_length=1000)
    variant: CandidateVariant
    angle: AngleType
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(alias="evidenceIds", min_length=1)


class StructuredGenerationOutput(GenerationModel):
    candidates: list[StructuredGenerationCandidate] = Field(min_length=1, max_length=10)
