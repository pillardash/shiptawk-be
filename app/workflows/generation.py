from uuid import UUID

from app.modules.drafts.services.event_to_draft_service import EventToDraftService
from app.modules.integrations.events import NormalizedEvent
from app.workflows.github import GitHubWorkflowContext


class DatabaseGenerationWorkflow:
    """Thin durable-workflow adapter for event-to-draft generation."""

    def __init__(self, service: EventToDraftService) -> None:
        self._service = service

    async def generate(
        self,
        context: GitHubWorkflowContext,
        event: NormalizedEvent,
        normalized_event_id: UUID,
    ) -> None:
        await self._service.generate(context, event, normalized_event_id)
