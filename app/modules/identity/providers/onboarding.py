from typing import Protocol

from app.modules.identity.events.onboarding import OnboardingGenerationRequest
from app.workflows.publisher import MetadataEvent, MetadataEventPublisher


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
