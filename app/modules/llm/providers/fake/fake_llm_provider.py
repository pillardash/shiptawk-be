from app.modules.llm.providers.generation_result import LLMGenerationResult
from app.modules.llm.providers.prompt_envelope import PromptEnvelope


class FakeLLMProvider:
    def __init__(self, result: LLMGenerationResult) -> None:
        self.result = result
        self.requests: list[PromptEnvelope] = []

    @property
    def name(self) -> str:
        return "fake"

    async def generate(self, envelope: PromptEnvelope) -> LLMGenerationResult:
        self.requests.append(envelope)
        return self.result
