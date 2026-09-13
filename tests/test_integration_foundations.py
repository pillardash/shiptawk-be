import json
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.identity.models.users import User
from app.modules.integrations.models import IntegrationConnection, IntegrationOAuthTransaction
from app.modules.integrations.providers.credential_vault_provider import (
    CredentialVaultDataError,
    CredentialVaultKeyUnavailableError,
    DeterministicFakeIntegrationCredentialVault,
    EncryptedIntegrationCredentials,
    FernetIntegrationCredentialVault,
)
from app.modules.integrations.repositories.integration_connection_repository import (
    get_current_provider_connection,
    get_workspace_connection,
    lock_current_provider_connection,
    replace_encrypted_credentials,
    revoke_local_credentials,
)
from app.modules.integrations.services.oauth_transaction_service import (
    IntegrationOAuthTransactionBinding,
    IntegrationOAuthTransactionService,
)
from app.modules.workspaces.models import Workspace
from app.shared.exceptions import BadRequestError

INTEGRATION_TABLES: list[Table] = [
    cast(Table, User.__table__),
    cast(Table, Workspace.__table__),
    cast(Table, IntegrationConnection.__table__),
    cast(Table, IntegrationOAuthTransaction.__table__),
]


@pytest_asyncio.fixture
async def integration_db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=INTEGRATION_TABLES)
    async with sessions() as db:
        yield db
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all, tables=INTEGRATION_TABLES)
    await engine.dispose()


def transaction_service() -> IntegrationOAuthTransactionService:
    return IntegrationOAuthTransactionService(
        encryption_key=Fernet.generate_key().decode("ascii"),
        expire_after=timedelta(minutes=10),
    )


def binding(**changes: object) -> IntegrationOAuthTransactionBinding:
    values: dict[str, object] = {
        "workspace_id": uuid4(),
        "actor_id": uuid4(),
        "provider": "example-provider",
        "purpose": "product-import",
        "subject_type": "product",
        "subject_id": uuid4(),
    }
    values.update(changes)
    return IntegrationOAuthTransactionBinding(**values)  # type: ignore[arg-type]


def with_wrong_binding(
    expected: IntegrationOAuthTransactionBinding, field: str
) -> IntegrationOAuthTransactionBinding:
    if field == "workspace_id":
        return replace(expected, workspace_id=uuid4())
    if field == "actor_id":
        return replace(expected, actor_id=uuid4())
    if field == "provider":
        return replace(expected, provider="wrong-provider")
    if field == "purpose":
        return replace(expected, purpose="wrong-purpose")
    if field == "subject_type":
        return replace(expected, subject_type="wrong-subject")
    if field == "subject_id":
        return replace(expected, subject_id=uuid4())
    raise AssertionError(f"Unknown binding field: {field}")


@pytest.mark.asyncio
async def test_oauth_transaction_roundtrip_preserves_product_subject_binding(
    integration_db: AsyncSession,
) -> None:
    service = transaction_service()
    expected = binding()

    created = await service.create(
        integration_db,
        binding=expected,
        return_path="/users/products/current?tab=integrations",
        redirect_uri="https://api.example.test/oauth/callback",
    )
    consumed = await service.consume(integration_db, binding=expected, state=created.state)

    assert created.code_verifier
    assert consumed.code_verifier == created.code_verifier
    assert consumed.return_path == "/users/products/current?tab=integrations"
    assert consumed.redirect_uri == "https://api.example.test/oauth/callback"
    persisted = await integration_db.scalar(select(IntegrationOAuthTransaction))
    assert persisted is not None
    assert persisted.purpose == "product-import"
    assert persisted.subject_type == "product"
    assert persisted.subject_id == expected.subject_id
    assert persisted.consumed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["workspace_id", "actor_id", "provider", "purpose", "subject_type", "subject_id"]
)
async def test_oauth_transaction_rejects_wrong_binding_without_consuming(
    integration_db: AsyncSession, field: str
) -> None:
    service = transaction_service()
    expected = binding()
    created = await service.create(
        integration_db,
        binding=expected,
        return_path="/users/settings",
        redirect_uri="https://api.example.test/oauth/callback",
    )
    wrong = with_wrong_binding(expected, field)

    with pytest.raises(BadRequestError, match="Invalid or expired OAuth state"):
        await service.consume(integration_db, binding=wrong, state=created.state)

    consumed = await service.consume(integration_db, binding=expected, state=created.state)
    assert consumed.code_verifier == created.code_verifier


@pytest.mark.asyncio
async def test_oauth_transaction_is_atomic_one_time_consumption(
    integration_db: AsyncSession,
) -> None:
    service = transaction_service()
    expected = binding()
    created = await service.create(
        integration_db,
        binding=expected,
        return_path="/users/settings",
        redirect_uri="https://api.example.test/oauth/callback",
    )

    await service.consume(integration_db, binding=expected, state=created.state)

    with pytest.raises(BadRequestError, match="Invalid or expired OAuth state"):
        await service.consume(integration_db, binding=expected, state=created.state)


@pytest.mark.asyncio
async def test_global_oauth_consume_returns_persisted_binding_and_is_actor_provider_bound(
    integration_db: AsyncSession,
) -> None:
    service = transaction_service()
    expected = binding()
    created = await service.create(
        integration_db,
        binding=expected,
        return_path="/users/products/current?tab=search",
        redirect_uri="https://api.example.test/integrations/search/example/callback",
    )

    with pytest.raises(BadRequestError, match="Invalid or expired OAuth state"):
        await service.consume_for_actor(
            integration_db,
            provider=expected.provider,
            actor_id=uuid4(),
            state=created.state,
        )

    consumed = await service.consume_for_actor(
        integration_db,
        provider=expected.provider,
        actor_id=expected.actor_id,
        state=created.state,
    )
    assert consumed.binding == expected
    assert consumed.code_verifier == created.code_verifier
    assert consumed.return_path == "/users/products/current?tab=search"

    with pytest.raises(BadRequestError, match="Invalid or expired OAuth state"):
        await service.consume_for_actor(
            integration_db,
            provider=expected.provider,
            actor_id=expected.actor_id,
            state=created.state,
        )


@pytest.mark.asyncio
async def test_oauth_transaction_rejects_expired_state(integration_db: AsyncSession) -> None:
    service = IntegrationOAuthTransactionService(
        encryption_key=Fernet.generate_key().decode("ascii"),
        expire_after=timedelta(seconds=-1),
    )
    expected = binding()
    created = await service.create(
        integration_db,
        binding=expected,
        return_path="/users/settings",
        redirect_uri="https://api.example.test/oauth/callback",
    )

    with pytest.raises(BadRequestError, match="Invalid or expired OAuth state"):
        await service.consume(integration_db, binding=expected, state=created.state)


def test_credential_vault_roundtrip_and_legacy_x_payload_compatibility() -> None:
    key = Fernet.generate_key().decode("ascii")
    vault = FernetIntegrationCredentialVault(keys={"v1": key, "v2": key}, active_key_version="v2")
    credentials = {"refreshToken": "refresh-secret", "accessToken": "access-secret"}
    legacy_payload = json.dumps(credentials, sort_keys=True, separators=(",", ":")).encode()
    legacy_ciphertext = Fernet(key.encode("ascii")).encrypt(legacy_payload).decode("ascii")

    encrypted = vault.encrypt(credentials)

    assert encrypted.key_version == "v2"
    assert vault.decrypt(encrypted.ciphertext, encrypted.key_version) == credentials
    assert vault.decrypt(legacy_ciphertext, "v1") == credentials


def test_credential_vault_reports_unavailable_key_and_malformed_data() -> None:
    key = Fernet.generate_key().decode("ascii")
    vault = FernetIntegrationCredentialVault(keys={"v1": key}, active_key_version="v1")

    with pytest.raises(CredentialVaultKeyUnavailableError):
        vault.decrypt("ciphertext", "retired")
    with pytest.raises(CredentialVaultDataError):
        vault.decrypt("malformed", "v1")

    unavailable_encryptor = FernetIntegrationCredentialVault(
        keys={"v1": key}, active_key_version="retired"
    )
    with pytest.raises(CredentialVaultKeyUnavailableError):
        unavailable_encryptor.encrypt({"accessToken": "secret"})


def test_deterministic_fake_credential_vault_roundtrip() -> None:
    vault = DeterministicFakeIntegrationCredentialVault()
    credentials = {"refreshToken": "refresh-secret", "accessToken": "access-secret"}

    first = vault.encrypt(credentials)
    second = vault.encrypt(dict(reversed(list(credentials.items()))))

    assert first == second
    assert vault.decrypt(first.ciphertext, first.key_version) == credentials


@pytest.mark.asyncio
async def test_connection_repository_is_workspace_scoped_and_revocation_clears_credentials(
    integration_db: AsyncSession,
) -> None:
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    actor_id = uuid4()
    connection = IntegrationConnection(
        workspace_id=workspace_id,
        provider="example-provider",
        external_account_id="account-1",
        credentials_ciphertext="old-ciphertext",
        credential_key_version="v1",
        idempotency_key=f"example:{uuid4()}",
    )
    integration_db.add(connection)
    await integration_db.commit()

    assert await get_workspace_connection(integration_db, workspace_id, connection.id) == connection
    assert await get_workspace_connection(integration_db, other_workspace_id, connection.id) is None
    assert (
        await get_current_provider_connection(integration_db, workspace_id, "example-provider")
        == connection
    )
    assert (
        await lock_current_provider_connection(integration_db, workspace_id, "example-provider")
        == connection
    )

    await replace_encrypted_credentials(
        integration_db,
        workspace_id=workspace_id,
        connection_id=connection.id,
        credentials=EncryptedIntegrationCredentials("new-ciphertext", "v2"),
        actor_id=actor_id,
    )
    await integration_db.commit()
    assert connection.credentials_ciphertext == "new-ciphertext"
    assert connection.credential_key_version == "v2"

    revoked_at = datetime.now(UTC)
    assert await revoke_local_credentials(
        integration_db,
        workspace_id=workspace_id,
        connection_id=connection.id,
        actor_id=actor_id,
        revoked_at=revoked_at,
    )
    await integration_db.commit()

    assert connection.status == "revoked"
    assert connection.revoked_at == revoked_at
    assert connection.credentials_ciphertext is None
    assert connection.credential_key_version is None
    assert (
        await get_current_provider_connection(integration_db, workspace_id, "example-provider")
        is None
    )
