from dataclasses import dataclass

from app.bootstrap.clients import SharedHTTPClients
from app.core.config import Settings
from app.modules.drafts.providers.protocols import CredentialDecryptor, Publisher
from app.modules.drafts.providers.x import HttpXPublisher
from app.modules.identity.providers.google_browser_oauth_provider import (
    GoogleBrowserOAuthProvider,
)
from app.modules.integrations.providers.credential_vault_provider import (
    FernetIntegrationCredentialVault,
)
from app.modules.integrations.providers.x_oauth import (
    HttpXOAuthProvider,
    IntegrationCredentialCipher,
    XOAuthProvider,
)
from app.services.github_app import GitHubAppProvider, HttpGitHubAppProvider
from app.services.oauth import GitHubOAuthProvider, OAuthProviderRegistry


@dataclass(frozen=True, slots=True)
class IntegrationResources:
    oauth_providers: OAuthProviderRegistry
    x_oauth_provider: XOAuthProvider | None
    credential_cipher: IntegrationCredentialCipher | None
    credential_decryptor: CredentialDecryptor | None
    github_app: GitHubAppProvider | None
    publisher: Publisher | None
    x_publishing_enabled: bool


def create_integrations(settings: Settings, clients: SharedHTTPClients) -> IntegrationResources:
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
                http_client=clients.github,
            )
        )
    if "google" in settings.oauth_enabled_providers:
        assert settings.google_login_client_id is not None
        assert settings.google_login_client_secret is not None
        oauth_providers.register(
            GoogleBrowserOAuthProvider(
                client_id=settings.google_login_client_id,
                client_secret=settings.google_login_client_secret.get_secret_value(),
                timeout_seconds=settings.oauth_http_timeout_seconds,
                http_client=clients.search,
            )
        )

    credential_cipher: IntegrationCredentialCipher | None = None
    credential_decryptor: CredentialDecryptor | None = None
    if settings.integration_credentials_encryption_key is not None:
        credential_vault = FernetIntegrationCredentialVault(
            keys=settings.integration_credentials_key_ring,
            active_key_version=settings.integration_credentials_active_key_version,
        )
        credential_cipher = credential_vault
        credential_decryptor = credential_vault

    x_oauth_provider: XOAuthProvider | None = None
    if settings.x_oauth_enabled:
        assert settings.x_oauth_client_id is not None
        assert settings.x_oauth_client_secret is not None
        x_oauth_provider = HttpXOAuthProvider(
            client_id=settings.x_oauth_client_id,
            client_secret=settings.x_oauth_client_secret.get_secret_value(),
            scopes=settings.x_oauth_scopes,
            timeout_seconds=settings.oauth_http_timeout_seconds,
            http_client=clients.x,
        )

    github_app: GitHubAppProvider | None = None
    if settings.github_app_id is not None and settings.github_app_private_key is not None:
        github_app = HttpGitHubAppProvider(
            app_id=settings.github_app_id,
            private_key=settings.github_app_private_key.get_secret_value(),
            timeout_seconds=settings.oauth_http_timeout_seconds,
            http_client=clients.github,
        )

    publisher: Publisher | None = None
    if settings.x_publishing_enabled:
        publisher = HttpXPublisher(
            timeout_seconds=settings.oauth_http_timeout_seconds,
            http_client=clients.x,
        )

    return IntegrationResources(
        oauth_providers=oauth_providers,
        x_oauth_provider=x_oauth_provider,
        credential_cipher=credential_cipher,
        credential_decryptor=credential_decryptor,
        github_app=github_app,
        publisher=publisher,
        x_publishing_enabled=settings.x_publishing_enabled,
    )
