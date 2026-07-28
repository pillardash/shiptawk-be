import json

from app.modules.drafts.schemas.context import EffectiveGenerationContext
from app.modules.drafts.schemas.evidence import PublicSafeSummary
from app.modules.drafts.schemas.provider import StructuredGenerationOutput
from app.modules.drafts.services.generation_context_service import GenerationContextBuilder
from app.modules.llm.providers.prompt_envelope import PromptEnvelope, PromptMessage

PROMPT_VERSION = "tweet-candidates.v1"
ROUTE_NAME = "tweet-candidates"


def build_tweet_candidates_envelope(
    context: EffectiveGenerationContext,
    summary: PublicSafeSummary,
    selected_angle: str,
    correlation_id: str,
) -> PromptEnvelope:
    safe_input = {
        "context": GenerationContextBuilder().compile_concise(context),
        "publicEvidence": summary.model_dump(mode="json", exclude={"internal_summary"}),
        "selectedAngle": selected_angle,
    }
    return PromptEnvelope(
        model="route-owned",
        prompt_id="tweet-candidates",
        prompt_version=PROMPT_VERSION,
        input_schema_version="v1",
        output_schema_version="v1",
        messages=(
            PromptMessage(
                role="system",
                content=(
                    "Return JSON candidates grounded only in the supplied public evidence. "
                    "Every candidate must cite evidenceIds. Do not call external tools."
                ),
            ),
            PromptMessage(role="user", content=json.dumps(safe_input, separators=(",", ":"))),
        ),
        output_schema_name="tweet_candidates",
        output_json_schema=StructuredGenerationOutput.model_json_schema(by_alias=True),
        parameters={"temperature": 0},
        correlation_id=correlation_id,
    )
