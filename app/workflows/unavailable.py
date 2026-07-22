from uuid import UUID


class UnavailableGitHubConsumerWorkflow:
    async def process(self, consumer_id: UUID) -> dict[str, object]:
        del consumer_id
        raise RuntimeError("GitHub generation workflow is not configured.")
