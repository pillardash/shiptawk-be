from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class LLMUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)


class LLMGenerationTelemetry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    latency_ms: int | None = Field(default=None, ge=0)
    usage: LLMUsage = LLMUsage()
    correlation_id: str
    provider_request_id: str | None = None
    finish_reason: str | None = None
    refusal: bool = False
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    pricing_version: str | None = None


class LLMGenerationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    output: object | None
    telemetry: LLMGenerationTelemetry
