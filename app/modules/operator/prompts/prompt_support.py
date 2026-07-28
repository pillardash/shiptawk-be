import json

from pydantic import TypeAdapter

from app.modules.llm.providers.prompt_envelope import PromptEnvelope, PromptMessage

type PromptData = dict[str, object] | list[dict[str, object]] | str

OPERATOR_AI_CORPUS_VERSION = "operator-ai-eval.v1"


def build_prompt(
    *,
    prompt_id: str,
    output_type: object,
    instructions: str,
    data: PromptData,
    prompt_version: str,
    correlation_id: str,
) -> PromptEnvelope:
    adapter: TypeAdapter[object] = TypeAdapter(output_type)
    content = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(content) > 10_000:
        raise ValueError("operator_prompt_input_too_large")
    return PromptEnvelope(
        model="route-selected",
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        input_schema_version="1",
        output_schema_version="1",
        messages=(
            PromptMessage(role="system", content=instructions),
            PromptMessage(role="user", content=content),
        ),
        output_schema_name=prompt_id,
        output_json_schema=adapter.json_schema(),
        parameters={"temperature": 0},
        correlation_id=correlation_id,
    )
