from dataclasses import dataclass
from datetime import timedelta

from app.bootstrap.clients import SharedHTTPClients
from app.core.config import Settings
from app.modules.integrations.providers.credential_vault_provider import (
    FernetIntegrationCredentialVault,
    IntegrationCredentialVault,
)
from app.modules.integrations.services.oauth_transaction_service import (
    IntegrationOAuthTransactionService,
)
from app.modules.search_intelligence.providers.search.google_search_provider import (
    GoogleSearchProvider,
)
from app.modules.search_intelligence.providers.search.search_registry_provider import (
    SearchProviderRegistry,
)


@dataclass(frozen=True, slots=True)
class SearchIntelligenceResources:
    providers: SearchProviderRegistry
    google: GoogleSearchProvider | None
    oauth_transactions: IntegrationOAuthTransactionService | None
    credential_vault: IntegrationCredentialVault | None


def create_search_intelligence(
    settings: Settings, clients: SharedHTTPClients
) -> SearchIntelligenceResources:
    oauth_transactions = None
    if settings.oauth_transaction_encryption_key is not None:
        oauth_transactions = IntegrationOAuthTransactionService(
            encryption_key=settings.oauth_transaction_encryption_key.get_secret_value(),
            expire_after=timedelta(minutes=settings.oauth_transaction_expire_minutes),
        )
    credential_vault = None
    if settings.integration_credentials_encryption_key is not None:
        credential_vault = FernetIntegrationCredentialVault(
            keys=settings.integration_credentials_key_ring,
            active_key_version=settings.integration_credentials_active_key_version,
        )
    google = None
    providers = SearchProviderRegistry()
    if settings.google_search_enabled:
        assert settings.google_search_client_id is not None
        assert settings.google_search_client_secret is not None
        google = GoogleSearchProvider(
            client_id=settings.google_search_client_id,
            client_secret=settings.google_search_client_secret.get_secret_value(),
            http_client=clients.search,
        )
        providers.register(google)
    return SearchIntelligenceResources(providers, google, oauth_transactions, credential_vault)


__all__ = ["SearchIntelligenceResources", "create_search_intelligence"]
