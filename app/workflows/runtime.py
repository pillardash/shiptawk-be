from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID

import inngest
import inngest.fast_api
from fastapi import FastAPI

from app.workflows.contracts import GitHubEventMetadata, OnboardingEventMetadata
from app.workflows.digests import DigestWorkflowResult


class OnboardingWorkflow(Protocol):
    async def generate(self, user_id: UUID, workspace_id: UUID) -> dict[str, object]: ...


class GitHubWorkflow(Protocol):
    async def process(self, consumer_id: UUID) -> dict[str, object]: ...


class DigestWorkflow(Protocol):
    async def deliver(self, frequency: str) -> DigestWorkflowResult: ...


class DraftDigestWorkflow(Protocol):
    async def deliver(self) -> DigestWorkflowResult: ...


class DeferredOnboardingWorkflow:
    async def generate(self, user_id: UUID, workspace_id: UUID) -> dict[str, object]:
        return {
            "status": "skipped",
            "reason": "source events generate drafts after repository opt-in",
            "userId": str(user_id),
            "workspaceId": str(workspace_id),
        }


@dataclass(frozen=True, slots=True)
class WorkflowFunctions:
    client: inngest.Inngest
    functions: list[object]


def create_workflow_functions(
    *,
    client: inngest.Inngest,
    github: GitHubWorkflow,
    onboarding: OnboardingWorkflow,
    digests: DigestWorkflow | None,
    draft_digests: DraftDigestWorkflow | None = None,
    repo_digests: DigestWorkflow | None = None,
) -> WorkflowFunctions:
    @client.create_function(
        fn_id="process-github-event-received",
        name="Process GitHub event in backend",
        trigger=inngest.TriggerEvent(event="github/event.received"),
        idempotency="event.data.consumerId",
        retries=4,
    )
    async def process_github(ctx: inngest.Context) -> dict[str, object]:
        metadata = GitHubEventMetadata.model_validate(ctx.event.data)
        if metadata.consumer_id != metadata.correlation_id:
            raise ValueError("GitHub workflow correlation metadata does not match.")
        return await github.process(metadata.consumer_id)

    @client.create_function(
        fn_id="generate-onboarding-drafts",
        name="Handle onboarding generation trigger in backend",
        trigger=inngest.TriggerEvent(event="user/onboarding.completed"),
        idempotency="event.data.userId",
        retries=3,
    )
    async def process_onboarding(ctx: inngest.Context) -> dict[str, object]:
        metadata = OnboardingEventMetadata.model_validate(ctx.event.data)
        return await onboarding.generate(metadata.user_id, metadata.workspace_id)

    @client.create_function(
        fn_id="send-draft-digests",
        name="Send draft digests",
        trigger=inngest.TriggerCron(cron="0 9 * * *"),
        retries=4,
    )
    async def send_draft_digests(ctx: inngest.Context) -> dict[str, int]:
        del ctx
        if draft_digests is None:
            return {"sent": 0, "skipped": 0}
        result = await draft_digests.deliver()
        return {"sent": result.sent, "skipped": result.skipped}

    @client.create_function(
        fn_id="send-weekly-achievement-digests",
        name="Send weekly achievement digests",
        trigger=inngest.TriggerCron(cron="0 10 * * 1"),
        retries=4,
    )
    async def send_weekly_digests(ctx: inngest.Context) -> dict[str, int]:
        del ctx
        if digests is None:
            return {"sent": 0, "skipped": 0}
        result = await digests.deliver("weekly")
        return {"sent": result.sent, "skipped": result.skipped}

    @client.create_function(
        fn_id="send-monthly-achievement-digests",
        name="Send monthly achievement digests",
        trigger=inngest.TriggerCron(cron="0 10 1 * *"),
        retries=4,
    )
    async def send_monthly_digests(ctx: inngest.Context) -> dict[str, int]:
        del ctx
        if digests is None:
            return {"sent": 0, "skipped": 0}
        result = await digests.deliver("monthly")
        return {"sent": result.sent, "skipped": result.skipped}

    @client.create_function(
        fn_id="send-weekly-repo-changelog-digests",
        name="Send weekly repository changelog digests",
        trigger=inngest.TriggerCron(cron="0 10 * * 1"),
        retries=4,
    )
    async def send_weekly_repo_digests(ctx: inngest.Context) -> dict[str, int]:
        del ctx
        if repo_digests is None:
            return {"sent": 0, "skipped": 0}
        result = await repo_digests.deliver("weekly")
        return {"sent": result.sent, "skipped": result.skipped}

    @client.create_function(
        fn_id="send-monthly-repo-changelog-digests",
        name="Send monthly repository changelog digests",
        trigger=inngest.TriggerCron(cron="0 10 1 * *"),
        retries=4,
    )
    async def send_monthly_repo_digests(ctx: inngest.Context) -> dict[str, int]:
        del ctx
        if repo_digests is None:
            return {"sent": 0, "skipped": 0}
        result = await repo_digests.deliver("monthly")
        return {"sent": result.sent, "skipped": result.skipped}

    functions: list[object] = [
        process_github,
        process_onboarding,
        send_draft_digests,
        send_weekly_digests,
        send_monthly_digests,
        send_weekly_repo_digests,
        send_monthly_repo_digests,
    ]
    return WorkflowFunctions(client=client, functions=functions)


def serve_workflow_functions(app: FastAPI, workflow: WorkflowFunctions) -> None:
    inngest.fast_api.serve(
        app,
        workflow.client,
        cast(list[Any], workflow.functions),
        serve_path="/api/inngest",
    )
