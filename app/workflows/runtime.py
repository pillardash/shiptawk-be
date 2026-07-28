from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID

import inngest
import inngest.fast_api
from fastapi import FastAPI

from app.workflows.contracts import (
    GitHubEventMetadata,
    MeasurementFollowupEventMetadata,
    OnboardingEventMetadata,
    OpportunityDetectionEventMetadata,
    SearchSyncEventMetadata,
    WebsiteCrawlEventMetadata,
    WeeklyGrowthEventMetadata,
    WeeklyGrowthReadyEventMetadata,
)
from app.workflows.digests import DigestWorkflowResult


class OnboardingWorkflow(Protocol):
    async def generate(self, user_id: UUID, workspace_id: UUID) -> dict[str, object]: ...


class GitHubWorkflow(Protocol):
    async def process(self, consumer_id: UUID) -> dict[str, object]: ...


class DigestWorkflow(Protocol):
    async def deliver(self, frequency: str) -> DigestWorkflowResult: ...


class DraftDigestWorkflow(Protocol):
    async def deliver(self) -> DigestWorkflowResult: ...


class WebsiteCrawlWorkflow(Protocol):
    async def process(self, run_id: UUID) -> dict[str, object]: ...


class SearchSyncWorkflow(Protocol):
    async def process(self, run_id: UUID) -> dict[str, object]: ...


class SearchSyncSchedulerWorkflow(Protocol):
    async def schedule(self) -> dict[str, int]: ...


class OutboxWorkflow(Protocol):
    async def deliver(self) -> dict[str, int]: ...


class OpportunityDetectionWorkflowProtocol(Protocol):
    async def process(
        self, run_id: UUID, workspace_id: UUID, product_id: UUID
    ) -> dict[str, object]: ...


class WeeklyOperatorWorkflowProtocol(Protocol):
    async def process(
        self, run_id: UUID, workspace_id: UUID, product_id: UUID
    ) -> dict[str, object]: ...


class WeeklyOperatorSchedulerProtocol(Protocol):
    async def schedule(self) -> dict[str, int]: ...


class WeeklyGrowthEmailWorkflowProtocol(Protocol):
    async def deliver(
        self, plan_id: UUID, workspace_id: UUID, product_id: UUID, plan_revision: int
    ) -> dict[str, object]: ...


class MeasurementFollowupWorkflowProtocol(Protocol):
    async def process(
        self, measurement_id: UUID, workspace_id: UUID, product_id: UUID
    ) -> dict[str, object]: ...


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
    website_crawls: WebsiteCrawlWorkflow | None = None,
    search_syncs: SearchSyncWorkflow | None = None,
    search_sync_scheduler: SearchSyncSchedulerWorkflow | None = None,
    outbox_delivery: OutboxWorkflow | None = None,
    opportunity_detection: OpportunityDetectionWorkflowProtocol | None = None,
    weekly_operator: WeeklyOperatorWorkflowProtocol | None = None,
    weekly_operator_scheduler: WeeklyOperatorSchedulerProtocol | None = None,
    weekly_growth_email: WeeklyGrowthEmailWorkflowProtocol | None = None,
    measurement_followup: MeasurementFollowupWorkflowProtocol | None = None,
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
        fn_id="crawl-product-website",
        name="Crawl product website",
        trigger=inngest.TriggerEvent(event="website/crawl.requested"),
        idempotency="event.data.runId",
        retries=4,
    )
    async def process_website_crawl(ctx: inngest.Context) -> dict[str, object]:
        metadata = WebsiteCrawlEventMetadata.model_validate(ctx.event.data)
        if metadata.run_id != metadata.correlation_id:
            raise ValueError("Website crawl workflow correlation metadata does not match.")
        if website_crawls is None:
            return {
                "status": "skipped",
                "reason": "website crawl workflow is not configured",
                "runId": str(metadata.run_id),
            }
        return await website_crawls.process(metadata.run_id)

    @client.create_function(
        fn_id="sync-search-metrics",
        name="Synchronize search performance metrics",
        trigger=inngest.TriggerEvent(event="search/metrics.sync.requested"),
        idempotency="event.data.runId",
        retries=4,
    )
    async def process_search_sync(ctx: inngest.Context) -> dict[str, object]:
        metadata = SearchSyncEventMetadata.model_validate(ctx.event.data)
        if metadata.run_id != metadata.correlation_id:
            raise ValueError("Search sync workflow correlation metadata does not match.")
        if search_syncs is None:
            return {
                "status": "skipped",
                "reason": "search sync workflow is not configured",
                "runId": str(metadata.run_id),
            }
        return await search_syncs.process(metadata.run_id)

    @client.create_function(
        fn_id="detect-product-opportunities",
        name="Detect product marketing opportunities",
        trigger=inngest.TriggerEvent(event="operator/opportunities.detect.requested"),
        idempotency="event.data.runId",
        retries=4,
    )
    async def process_opportunity_detection(ctx: inngest.Context) -> dict[str, object]:
        metadata = OpportunityDetectionEventMetadata.model_validate(ctx.event.data)
        if metadata.run_id != metadata.correlation_id:
            raise ValueError("Opportunity detection correlation metadata does not match.")
        if opportunity_detection is None:
            raise RuntimeError("Opportunity detection workflow is not configured.")
        return await opportunity_detection.process(
            metadata.run_id, metadata.workspace_id, metadata.product_id
        )

    @client.create_function(
        fn_id="run-weekly-growth-operator",
        name="Run weekly growth operator",
        trigger=inngest.TriggerEvent(event="operator/weekly-growth.requested"),
        idempotency="event.data.runId",
        retries=4,
    )
    async def process_weekly_operator(ctx: inngest.Context) -> dict[str, object]:
        metadata = WeeklyGrowthEventMetadata.model_validate(ctx.event.data)
        if metadata.run_id != metadata.correlation_id:
            raise ValueError("Weekly operator correlation metadata does not match.")
        if weekly_operator is None:
            raise RuntimeError("Weekly operator workflow is not configured.")
        return await weekly_operator.process(
            metadata.run_id, metadata.workspace_id, metadata.product_id
        )

    @client.create_function(
        fn_id="deliver-weekly-growth-email",
        name="Deliver canonical weekly growth email",
        trigger=inngest.TriggerEvent(event="operator/weekly-growth.ready"),
        idempotency="event.data.planId + ':' + event.data.planRevision",
        retries=4,
    )
    async def deliver_weekly_growth_email(ctx: inngest.Context) -> dict[str, object]:
        metadata = WeeklyGrowthReadyEventMetadata.model_validate(ctx.event.data)
        if weekly_growth_email is None:
            return {"status": "suppressed", "planId": str(metadata.plan_id)}
        return await weekly_growth_email.deliver(
            metadata.plan_id,
            metadata.workspace_id,
            metadata.product_id,
            metadata.plan_revision,
        )

    @client.create_function(
        fn_id="measure-operator-action",
        name="Measure completed operator action",
        trigger=inngest.TriggerEvent(event="operator/measurement.followup"),
        idempotency="event.data.measurementId",
        retries=4,
    )
    async def measure_operator_action(ctx: inngest.Context) -> dict[str, object]:
        metadata = MeasurementFollowupEventMetadata.model_validate(ctx.event.data)
        if measurement_followup is None:
            raise RuntimeError("Measurement follow-up workflow is not configured.")
        return await measurement_followup.process(
            metadata.measurement_id, metadata.workspace_id, metadata.product_id
        )

    @client.create_function(
        fn_id="schedule-weekly-growth-operators",
        name="Schedule enabled weekly growth operators",
        trigger=inngest.TriggerCron(cron="0 * * * *"),
        retries=2,
    )
    async def schedule_weekly_operators(ctx: inngest.Context) -> dict[str, int]:
        del ctx
        if weekly_operator_scheduler is None:
            return {"scheduled": 0, "skipped": 0}
        return await weekly_operator_scheduler.schedule()

    @client.create_function(
        fn_id="schedule-search-metric-syncs",
        name="Schedule daily search metric synchronization",
        trigger=inngest.TriggerCron(cron="15 2 * * *"),
        retries=2,
    )
    async def schedule_search_syncs(ctx: inngest.Context) -> dict[str, int]:
        del ctx
        if search_sync_scheduler is None:
            return {"scheduled": 0, "skipped": 0}
        return await search_sync_scheduler.schedule()

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

    @client.create_function(
        fn_id="deliver-transactional-outbox",
        name="Deliver transactional outbox events",
        trigger=inngest.TriggerCron(cron="* * * * *"),
        retries=2,
    )
    async def deliver_transactional_outbox(ctx: inngest.Context) -> dict[str, int]:
        del ctx
        if outbox_delivery is None:
            return {"claimed": 0, "published": 0, "retried": 0, "failed": 0}
        return await outbox_delivery.deliver()

    functions: list[object] = [
        process_github,
        process_onboarding,
        process_website_crawl,
        send_draft_digests,
        send_weekly_digests,
        send_monthly_digests,
        send_weekly_repo_digests,
        send_monthly_repo_digests,
        deliver_transactional_outbox,
        process_search_sync,
        process_opportunity_detection,
        schedule_search_syncs,
        process_weekly_operator,
        schedule_weekly_operators,
        deliver_weekly_growth_email,
        measure_operator_action,
    ]
    return WorkflowFunctions(client=client, functions=functions)


def serve_workflow_functions(app: FastAPI, workflow: WorkflowFunctions) -> None:
    inngest.fast_api.serve(
        app,
        workflow.client,
        cast(list[Any], workflow.functions),
        serve_path="/api/inngest",
    )
