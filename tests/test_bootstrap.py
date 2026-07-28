import ast
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bootstrap.clients import create_http_clients
from app.bootstrap.resources import ApplicationResources
from app.bootstrap.search_intelligence import create_search_intelligence
from app.core.config import Settings
from app.main import create_app
from app.modules.drafts.providers.x import HttpXPublisher
from app.modules.integrations.providers.credential_vault_provider import (
    FernetIntegrationCredentialVault,
)
from app.modules.integrations.providers.x_oauth import HttpXOAuthProvider
from app.modules.llm.providers.openai.openai_client import OpenAIClient
from app.services.github_app import HttpGitHubAppProvider
from app.services.oauth import GitHubOAuthProvider


def test_imported_application_is_composed_without_starting_http_clients() -> None:
    from app.main import app

    assert isinstance(app.state.resources, ApplicationResources)
    assert all(not client.is_closed for client in app.state.resources.http_clients.all())


def test_create_app_exposes_typed_resources_and_legacy_state() -> None:
    app = create_app()

    assert isinstance(app.state.resources, ApplicationResources)
    assert app.state.cache is app.state.resources.infrastructure.cache
    assert app.state.jobs is app.state.resources.infrastructure.jobs
    assert app.state.storage is app.state.resources.infrastructure.storage
    assert app.state.oauth_providers is app.state.resources.integrations.oauth_providers
    assert app.state.inngest is app.state.resources.workflows.inngest


def test_operator_llm_routes_resolve_as_distinct_explicit_plans() -> None:
    routes = create_app().state.resources.llm.routes
    names = (
        "operator-recommendation",
        "operator-asset-seo",
        "operator-asset-blog",
        "operator-asset-product-update",
        "operator-asset-x",
        "operator-risk-review",
    )
    resolved = [routes.resolve(name, required_capabilities={"structured_output"}) for name in names]
    assert [route.name for route in resolved] == list(names)
    assert all(route.registrations[0].pricing_version != "unconfigured" for route in resolved)


def test_lifespan_closes_all_shared_http_clients() -> None:
    app = create_app()
    clients = app.state.resources.http_clients.all()

    assert clients
    assert all(not client.is_closed for client in clients)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert all(not http_client.is_closed for http_client in clients)

    assert all(http_client.is_closed for http_client in clients)


@pytest.mark.asyncio
async def test_http_providers_accept_the_same_externally_owned_client() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))

    openai = OpenAIClient(api_key="secret", timeout_seconds=1, http_client=client)
    github_oauth = GitHubOAuthProvider(client_id="id", client_secret="secret", http_client=client)
    github_app = HttpGitHubAppProvider(app_id=1, private_key="key", http_client=client)
    x_oauth = HttpXOAuthProvider(
        client_id="id", client_secret="secret", scopes=["users.read"], http_client=client
    )
    x_publisher = HttpXPublisher(timeout_seconds=1, http_client=client)

    assert openai.http_client is client
    assert github_oauth.http_client is client
    assert github_app.http_client is client
    assert x_oauth.http_client is client
    assert x_publisher.http_client is client
    assert not client.is_closed
    await client.aclose()


def test_provider_request_methods_do_not_create_async_client_contexts() -> None:
    provider_paths = [
        "app/modules/llm/providers/openai/openai_client.py",
        "app/services/oauth.py",
        "app/services/github_app.py",
        "app/modules/integrations/providers/x_oauth.py",
        "app/modules/drafts/providers/x.py",
    ]

    for provider_path in provider_paths:
        tree = ast.parse(Path(provider_path).read_text())
        async_with_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncWith)
            and any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "AsyncClient"
                for item in node.items
            )
        ]
        assert async_with_calls == [], provider_path


@pytest.mark.asyncio
async def test_search_bootstrap_encrypts_with_active_key_and_decrypts_previous_key() -> None:
    previous_key = "iezSZZMKlqtExgXjvfgFPjnpXI7Vb9RWcYNnNr84jm8="
    settings = Settings(
        integration_credentials_encryption_key=("MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="),
        integration_credentials_active_key_version="v2",
        integration_credentials_previous_encryption_keys={"v1": previous_key},
    )
    clients = create_http_clients(settings)
    resources = create_search_intelligence(settings, clients)
    assert resources.credential_vault is not None
    legacy = FernetIntegrationCredentialVault(keys={"v1": previous_key}, active_key_version="v1")
    legacy_value = legacy.encrypt({"accessToken": "legacy"})

    current = resources.credential_vault.encrypt({"accessToken": "current"})

    assert current.key_version == "v2"
    assert resources.credential_vault.decrypt(legacy_value.ciphertext, "v1") == {
        "accessToken": "legacy"
    }
    await clients.close()
