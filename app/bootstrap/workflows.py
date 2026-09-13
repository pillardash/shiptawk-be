from dataclasses import dataclass

import inngest
from fastapi import FastAPI

from app.bootstrap.clients import SharedHTTPClients
from app.bootstrap.llm import LLMResources
from app.bootstrap.search_intelligence import SearchIntelligenceResources
from app.core.config import Settings
from app.db.session import get_sessionmaker
from app.modules.drafts.services.event_to_draft_service import EventToDraftService
from app.modules.identity.providers.onboarding import (
    DurableOnboardingGenerationTrigger,
    OnboardingGenerationTrigger,
)
from app.modules.operator.services.measurement_service import MeasurementFollowupWorkflow
from app.modules.operator.services.operator_ai_service import OperatorAIService
from app.modules.operator.services.weekly_delivery_service import WeeklyGrowthEmailWorkflow
from app.modules.products.providers.website_fetch_provider import HTTPXWebsiteFetchProvider
from app.modules.products.services.website_crawl_service import WebsiteCrawlService
from app.modules.search_intelligence.services.search_sync_service import (
    SearchSyncScheduler,
    SearchSyncService,
)
from app.services.email.base import create_email_service
from app.workflows.digests import AchievementDigestWorkflow
from app.workflows.generation import DatabaseGenerationWorkflow
from app.workflows.github import GitHubConsumerWorkflow
from app.workflows.opportunity_detection import OpportunityDetectionWorkflow
from app.workflows.outbox import OutboxDeliveryWorkflow
from app.workflows.publisher import InngestMetadataEventPublisher, MetadataEventPublisher
from app.workflows.runtime import (
    DeferredOnboardingWorkflow,
    GitHubWorkflow,
    WorkflowFunctions,
    create_workflow_functions,
    serve_workflow_functions,
)
from app.workflows.unavailable import UnavailableGitHubConsumerWorkflow
from app.workflows.weekly_operator import WeeklyOperatorScheduler, WeeklyOperatorWorkflow


@dataclass(frozen=True, slots=True)
class WorkflowResources:
    inngest: inngest.Inngest
    functions: WorkflowFunctions
    event_publisher: MetadataEventPublisher | None
    onboarding_trigger: OnboardingGenerationTrigger | None


def create_workflows(
    settings: Settings,
    llm: LLMResources,
    clients: SharedHTTPClients,
    search_intelligence: SearchIntelligenceResources,
) -> WorkflowResources:
    client = inngest.Inngest(
        app_id="shiptawk-backend",
        app_version=settings.app_version,
        event_api_base_url=settings.inngest_event_api_base_url,
        event_key=(
            settings.inngest_event_key.get_secret_value()
            if settings.inngest_event_key is not None
            else None
        ),
        signing_key=(
            settings.inngest_signing_key.get_secret_value()
            if settings.inngest_signing_key is not None
            else None
        ),
        is_production=settings.app_env not in {"local", "test"},
        request_timeout=int(settings.inngest_request_timeout_seconds * 1000),
    )
    publisher = InngestMetadataEventPublisher(client)
    event_publisher: MetadataEventPublisher | None = publisher if settings.inngest_enabled else None
    onboarding_trigger: OnboardingGenerationTrigger | None = (
        DurableOnboardingGenerationTrigger(publisher) if settings.inngest_enabled else None
    )
    sessions = get_sessionmaker()
    github_workflow: GitHubWorkflow
    if (
        settings.generation_provider is not None
        and settings.github_webhook_payload_encryption_key is not None
    ):
        github_workflow = GitHubConsumerWorkflow(
            sessions=sessions,
            encryption_key=settings.github_webhook_payload_encryption_key.get_secret_value(),
            generation=DatabaseGenerationWorkflow(
                EventToDraftService(sessions=sessions, llm=llm.execution_service)
            ),
        )
    else:
        github_workflow = UnavailableGitHubConsumerWorkflow()
    email_service = create_email_service(settings)
    digest_workflow = (
        AchievementDigestWorkflow(
            sessions=sessions,
            email_service=email_service,
            frontend_url=settings.frontend_url,
            public_app_url=settings.public_backend_url,
            api_prefix=settings.api_v1_prefix,
            unsubscribe_secret=settings.token_encryption_key.get_secret_value(),
        )
        if email_service is not None and settings.token_encryption_key is not None
        else None
    )
    functions = create_workflow_functions(
        client=client,
        github=github_workflow,
        onboarding=DeferredOnboardingWorkflow(),
        digests=digest_workflow,
        website_crawls=WebsiteCrawlService(
            sessions=sessions,
            provider=HTTPXWebsiteFetchProvider(),
        ),
        search_syncs=(
            SearchSyncService(
                sessions=sessions,
                providers=search_intelligence.providers,
                vault=search_intelligence.credential_vault,
            )
            if search_intelligence.credential_vault is not None
            else None
        ),
        search_sync_scheduler=SearchSyncScheduler(
            sessions=sessions,
            providers=search_intelligence.providers,
        ),
        outbox_delivery=OutboxDeliveryWorkflow(sessions=sessions, publisher=publisher),
        opportunity_detection=OpportunityDetectionWorkflow(sessions=sessions),
        weekly_operator=WeeklyOperatorWorkflow(
            sessions=sessions,
            detection=OpportunityDetectionWorkflow(sessions=sessions),
            ai=OperatorAIService(sessions, llm.execution_service),
        ),
        weekly_operator_scheduler=WeeklyOperatorScheduler(sessions=sessions),
        weekly_growth_email=(
            WeeklyGrowthEmailWorkflow(
                sessions=sessions,
                email_service=email_service,
                frontend_url=settings.frontend_url,
            )
            if email_service is not None
            else None
        ),
        measurement_followup=MeasurementFollowupWorkflow(sessions=sessions),
        legacy_daily_draft_schedule_enabled=settings.legacy_daily_draft_schedule_enabled,
        legacy_achievement_digest_schedules_enabled=(
            settings.legacy_achievement_digest_schedules_enabled
        ),
        legacy_repository_changelog_schedules_enabled=(
            settings.legacy_repository_changelog_schedules_enabled
        ),
        weekly_growth_schedule_enabled=settings.weekly_growth_schedule_enabled,
    )
    return WorkflowResources(
        inngest=client,
        functions=functions,
        event_publisher=event_publisher,
        onboarding_trigger=onboarding_trigger,
    )


def register_workflows(app: FastAPI, settings: Settings, resources: WorkflowResources) -> None:
    if settings.inngest_enabled or settings.app_env in {"local", "test"}:
        serve_workflow_functions(app, resources.functions)
