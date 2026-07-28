from app.modules.integrations.models.connections import (
    IntegrationConnection,
    IntegrationOAuthTransaction,
)
from app.modules.integrations.models.webhook_ingress import (
    github_raw_event_consumers,
    github_raw_events,
    normalized_events,
)

integration_connections = IntegrationConnection.__table__
integration_oauth_transactions = IntegrationOAuthTransaction.__table__

__all__ = [
    "IntegrationConnection",
    "IntegrationOAuthTransaction",
    "github_raw_event_consumers",
    "github_raw_events",
    "integration_connections",
    "integration_oauth_transactions",
    "normalized_events",
]
