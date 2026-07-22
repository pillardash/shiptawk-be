from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.workflows.publisher import MetadataEvent, MetadataEventPublisher


@dataclass(frozen=True, slots=True)
class OnboardingGenerationRequest:
    user_id: UUID
    workspace_id: UUID
    event_id: str


class OnboardingGenerationTrigger(Protocol):
    async def trigger(self, request: OnboardingGenerationRequest) -> None: ...


class DurableOnboardingGenerationTrigger:
    def __init__(self, publisher: MetadataEventPublisher) -> None:
        self._publisher = publisher

    async def trigger(self, request: OnboardingGenerationRequest) -> None:
        await self._publisher.publish(
            MetadataEvent(
                name="user/onboarding.completed",
                event_id=request.event_id,
                data={
                    "userId": str(request.user_id),
                    "workspaceId": str(request.workspace_id),
                    "schemaVersion": 1,
                },
            )
        )
