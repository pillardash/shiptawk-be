from app.modules.llm.providers.prompt_envelope import PromptEnvelope
from app.modules.operator.prompts.prompt_support import OPERATOR_AI_CORPUS_VERSION, build_prompt
from app.modules.operator.schemas.ai_output_schema import RiskReviewOutput


def build_risk_review_prompt(
    asset_type: str,
    content: dict[str, object],
    evidence: list[dict[str, object]],
    *,
    correlation_id: str,
) -> PromptEnvelope:
    return build_prompt(
        prompt_id="operator.risk_review",
        prompt_version=f"operator-risk-review.v1+{OPERATOR_AI_CORPUS_VERSION}",
        correlation_id=correlation_id,
        output_type=RiskReviewOutput,
        instructions=(
            "Review, but never modify or publish, this exact asset revision. "
            "Block secrets, private "
            "content, unsupported claims, or unsafe promises. Return structured findings."
        ),
        data={"asset_type": asset_type[:64], "content": content, "evidence": evidence[:20]},
    )
