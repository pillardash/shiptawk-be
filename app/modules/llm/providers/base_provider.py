from typing import Protocol

from app.modules.llm.providers.generation_result import LLMGenerationResult
from app.modules.llm.providers.prompt_envelope import PromptEnvelope


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def generate(self, envelope: PromptEnvelope) -> LLMGenerationResult: ...
