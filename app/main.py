import inngest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import router as api_router
from app.api.v1.health import health_check
from app.cache.dependencies import create_cache
from app.core.config import get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    BodySizeLimitMiddleware,
    BrowserAuthNoStoreMiddleware,
    ProcessTimeMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.rate_limit import create_rate_limit_store
from app.core.sentry import configure_sentry
from app.db.session import get_sessionmaker
from app.domains.drafts.publisher import FernetCredentialDecryptor, HttpXPublisher
from app.domains.generation.openai_provider import OpenAIHTTPGenerationProvider
from app.domains.generation.provider import GenerationProvider
from app.domains.integrations.x_oauth import FernetIntegrationCredentialCipher, HttpXOAuthProvider
from app.domains.users.onboarding import DurableOnboardingGenerationTrigger
from app.services.email.base import create_email_service
from app.services.github_app import HttpGitHubAppProvider
from app.services.jobs import create_job_service
from app.services.oauth import GitHubOAuthProvider, OAuthProviderRegistry
from app.services.storage.base import create_storage_service
from app.workflows.digests import AchievementDigestWorkflow
from app.workflows.generation import DatabaseGenerationWorkflow
from app.workflows.github import GitHubConsumerWorkflow
from app.workflows.publisher import InngestMetadataEventPublisher
from app.workflows.runtime import (
    DeferredOnboardingWorkflow,
    GitHubWorkflow,
    create_workflow_functions,
    serve_workflow_functions,
)
from app.workflows.unavailable import UnavailableGitHubConsumerWorkflow


def create_app() -> FastAPI:
    settings = get_settings()
    configure_sentry(settings)
    cache = create_cache(settings)
    job_service = create_job_service(settings)
    storage_service = create_storage_service(settings)
    configure_logging(level=settings.log_level, log_format=settings.log_format)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Shiptawk AI marketing operator API",
        debug=settings.debug,
        openapi_url=settings.openapi_url,
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
    )
    app.state.cache = cache
    app.state.jobs = job_service
    app.state.storage = storage_service
    app.state.workflow_event_publisher = None
    app.state.onboarding_generation_trigger = None
    app.state.generation_provider = None
    app.state.generation_model = "default"
    app.state.x_publishing_enabled = settings.x_publishing_enabled
    app.state.publisher = (
        HttpXPublisher(timeout_seconds=settings.oauth_http_timeout_seconds)
        if settings.x_publishing_enabled
        else None
    )
    inngest_client = inngest.Inngest(
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
    publisher = InngestMetadataEventPublisher(inngest_client)
    if settings.inngest_enabled:
        app.state.workflow_event_publisher = publisher
        app.state.onboarding_generation_trigger = DurableOnboardingGenerationTrigger(publisher)
    oauth_providers = OAuthProviderRegistry(
        require_encryption=settings.app_env == "production",
        encryption_available=settings.oauth_transaction_encryption_key is not None,
    )
    if "github" in settings.oauth_enabled_providers:
        assert settings.github_oauth_client_id is not None
        assert settings.github_oauth_client_secret is not None
        oauth_providers.register(
            GitHubOAuthProvider(
                client_id=settings.github_oauth_client_id,
                client_secret=settings.github_oauth_client_secret.get_secret_value(),
                timeout_seconds=settings.oauth_http_timeout_seconds,
            )
        )
    app.state.oauth_providers = oauth_providers
    app.state.x_oauth_provider = None
    app.state.integration_credential_cipher = None
    app.state.integration_credential_decryptor = None
    if settings.integration_credentials_encryption_key is not None:
        integration_key = settings.integration_credentials_encryption_key.get_secret_value()
        app.state.integration_credential_cipher = FernetIntegrationCredentialCipher(
            key=integration_key
        )
        app.state.integration_credential_decryptor = FernetCredentialDecryptor(
            {"v1": integration_key}
        )
    if settings.x_oauth_enabled:
        assert settings.x_oauth_client_id is not None
        assert settings.x_oauth_client_secret is not None
        app.state.x_oauth_provider = HttpXOAuthProvider(
            client_id=settings.x_oauth_client_id,
            client_secret=settings.x_oauth_client_secret.get_secret_value(),
            scopes=settings.x_oauth_scopes,
            timeout_seconds=settings.oauth_http_timeout_seconds,
        )
    app.state.github_app = None
    if settings.github_app_id is not None and settings.github_app_private_key is not None:
        app.state.github_app = HttpGitHubAppProvider(
            app_id=settings.github_app_id,
            private_key=settings.github_app_private_key.get_secret_value(),
            timeout_seconds=settings.oauth_http_timeout_seconds,
        )

    app.add_middleware(
        BrowserAuthNoStoreMiddleware,
        path_prefix=f"{settings.api_v1_prefix}/auth/browser",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_strings,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    if settings.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            store=create_rate_limit_store(cache),
            limit=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
            trusted_proxy_ips=settings.trusted_proxy_ips,
        )
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.max_request_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(ProcessTimeMiddleware)
    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)

    app.add_api_route("/health", health_check, methods=["GET"], tags=["health"])
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    provider: GenerationProvider | None = None
    if settings.generation_provider == "openai":
        assert settings.openai_api_key is not None
        provider = OpenAIHTTPGenerationProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout_seconds=settings.generation_timeout_seconds,
        )
    sessions = get_sessionmaker()
    github_workflow: GitHubWorkflow
    if provider is not None and settings.github_webhook_payload_encryption_key is not None:
        github_workflow = GitHubConsumerWorkflow(
            sessions=sessions,
            encryption_key=settings.github_webhook_payload_encryption_key.get_secret_value(),
            generation=DatabaseGenerationWorkflow(
                sessions=sessions,
                provider=provider,
                model=settings.openai_model,
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
            unsubscribe_secret=settings.jwt_secret_key,
        )
        if email_service is not None
        else None
    )
    workflow_functions = create_workflow_functions(
        client=inngest_client,
        github=github_workflow,
        onboarding=DeferredOnboardingWorkflow(),
        digests=digest_workflow,
    )
    app.state.inngest = inngest_client
    # Production deployments without enabled workflows must not initialize an
    # Inngest handler that requires a signing key.
    if settings.inngest_enabled or settings.app_env in {"local", "test"}:
        serve_workflow_functions(app, workflow_functions)

    return app


app = create_app()
