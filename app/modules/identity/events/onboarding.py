from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class OnboardingGenerationRequest:
    user_id: UUID
    workspace_id: UUID
    event_id: str
