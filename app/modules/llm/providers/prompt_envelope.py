from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PromptMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class PromptEnvelope(BaseModel):
    """Provider-neutral request assembled by a domain-owned use case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    prompt_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    input_schema_version: str = Field(min_length=1)
    output_schema_version: str = Field(min_length=1)
    messages: tuple[PromptMessage, ...] = Field(min_length=1)
    output_schema_name: str = Field(min_length=1)
    output_json_schema: dict[str, object]
    parameters: dict[str, object] = Field(default_factory=dict)
    correlation_id: str = Field(min_length=1)
