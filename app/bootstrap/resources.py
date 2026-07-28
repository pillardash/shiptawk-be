from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from app.bootstrap.clients import SharedHTTPClients, create_http_clients
from app.bootstrap.infrastructure import InfrastructureResources, create_infrastructure
from app.bootstrap.integrations import IntegrationResources, create_integrations
from app.bootstrap.llm import LLMResources, create_llm
from app.bootstrap.search_intelligence import (
    SearchIntelligenceResources,
    create_search_intelligence,
)
from app.bootstrap.workflows import WorkflowResources, create_workflows
from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class ApplicationResources:
    settings: Settings
    http_clients: SharedHTTPClients
    infrastructure: InfrastructureResources
    integrations: IntegrationResources
    llm: LLMResources
    workflows: WorkflowResources
    search_intelligence: SearchIntelligenceResources


def create_resources(settings: Settings) -> ApplicationResources:
    clients = create_http_clients(settings)
    infrastructure = create_infrastructure(settings)
    integrations = create_integrations(settings, clients)
    llm = create_llm(settings, clients)
    search_intelligence = create_search_intelligence(settings, clients)
    workflows = create_workflows(settings, llm, clients, search_intelligence)
    return ApplicationResources(
        settings=settings,
        http_clients=clients,
        infrastructure=infrastructure,
        integrations=integrations,
        llm=llm,
        workflows=workflows,
        search_intelligence=search_intelligence,
    )


def bind_application_state(app: FastAPI, resources: ApplicationResources) -> None:
    app.state.resources = resources
    app.state.cache = resources.infrastructure.cache
    app.state.jobs = resources.infrastructure.jobs
    app.state.storage = resources.infrastructure.storage
    app.state.workflow_event_publisher = resources.workflows.event_publisher
    app.state.onboarding_generation_trigger = resources.workflows.onboarding_trigger
    app.state.llm_execution_service = resources.llm.execution_service
    app.state.x_publishing_enabled = resources.integrations.x_publishing_enabled
    app.state.publisher = resources.integrations.publisher
    app.state.oauth_providers = resources.integrations.oauth_providers
    app.state.x_oauth_provider = resources.integrations.x_oauth_provider
    app.state.integration_credential_cipher = resources.integrations.credential_cipher
    app.state.integration_credential_decryptor = resources.integrations.credential_decryptor
    app.state.github_app = resources.integrations.github_app
    app.state.inngest = resources.workflows.inngest
    app.state.search_intelligence = resources.search_intelligence


def create_lifespan(
    resources: ApplicationResources,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await resources.http_clients.close()

    return lifespan
