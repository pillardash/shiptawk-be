from pydantic import BaseModel, ConfigDict


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    structured_output: bool = True
    tool_calls: bool = False
    max_tool_rounds: int = 0
