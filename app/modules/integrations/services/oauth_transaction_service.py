from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_pkce_verifier, generate_token, hash_token
from app.modules.integrations.models import IntegrationOAuthTransaction
from app.modules.integrations.policies.return_paths import validate_return_path
from app.shared.exceptions import BadRequestError


@dataclass(frozen=True, slots=True)
class IntegrationOAuthTransactionBinding:
    workspace_id: UUID
    actor_id: UUID
    provider: str
    purpose: str | None = None
    subject_type: str | None = None
    subject_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CreatedIntegrationOAuthTransaction:
    state: str
    code_verifier: str


@dataclass(frozen=True, slots=True)
class ConsumedIntegrationOAuthTransaction:
    code_verifier: str
    return_path: str
    redirect_uri: str


@dataclass(frozen=True, slots=True)
class ConsumedBoundIntegrationOAuthTransaction:
    binding: IntegrationOAuthTransactionBinding
    code_verifier: str
    return_path: str
    redirect_uri: str


class IntegrationOAuthTransactionService:
    def __init__(self, *, encryption_key: str, expire_after: timedelta) -> None:
        self._fernet = Fernet(encryption_key.encode("ascii"))
        self._expire_after = expire_after

    async def create(
        self,
        db: AsyncSession,
        *,
        binding: IntegrationOAuthTransactionBinding,
        return_path: str,
        redirect_uri: str,
    ) -> CreatedIntegrationOAuthTransaction:
        safe_return_path = validate_return_path(return_path)
        state = generate_token()
        verifier = generate_pkce_verifier()
        db.add(
            IntegrationOAuthTransaction(
                workspace_id=binding.workspace_id,
                user_id=binding.actor_id,
                provider=binding.provider,
                purpose=binding.purpose,
                subject_type=binding.subject_type,
                subject_id=binding.subject_id,
                state_hash=hash_token(state),
                code_verifier_ciphertext=self._fernet.encrypt(verifier.encode()).decode("ascii"),
                return_path=safe_return_path,
                redirect_uri=redirect_uri,
                expires_at=datetime.now(UTC) + self._expire_after,
                created_by=binding.actor_id,
            )
        )
        await db.commit()
        return CreatedIntegrationOAuthTransaction(state, verifier)

    async def consume(
        self,
        db: AsyncSession,
        *,
        binding: IntegrationOAuthTransactionBinding,
        state: str,
    ) -> ConsumedIntegrationOAuthTransaction:
        now = datetime.now(UTC)
        row = (
            await db.execute(
                update(IntegrationOAuthTransaction)
                .where(
                    IntegrationOAuthTransaction.workspace_id == binding.workspace_id,
                    IntegrationOAuthTransaction.user_id == binding.actor_id,
                    IntegrationOAuthTransaction.provider == binding.provider,
                    IntegrationOAuthTransaction.purpose == binding.purpose,
                    IntegrationOAuthTransaction.subject_type == binding.subject_type,
                    IntegrationOAuthTransaction.subject_id == binding.subject_id,
                    IntegrationOAuthTransaction.state_hash == hash_token(state),
                    IntegrationOAuthTransaction.consumed_at.is_(None),
                    IntegrationOAuthTransaction.expires_at > now,
                )
                .values(consumed_at=now, updated_by=binding.actor_id)
                .returning(
                    IntegrationOAuthTransaction.code_verifier_ciphertext,
                    IntegrationOAuthTransaction.return_path,
                    IntegrationOAuthTransaction.redirect_uri,
                )
            )
        ).one_or_none()
        if row is None:
            await db.rollback()
            raise BadRequestError("Invalid or expired OAuth state.", code="invalid_oauth_state")
        await db.commit()
        try:
            verifier = self._fernet.decrypt(row.code_verifier_ciphertext.encode("ascii")).decode()
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise BadRequestError("Invalid OAuth transaction.", code="invalid_oauth_state") from exc
        return ConsumedIntegrationOAuthTransaction(verifier, row.return_path, row.redirect_uri)

    async def consume_for_actor(
        self,
        db: AsyncSession,
        *,
        provider: str,
        actor_id: UUID,
        state: str,
    ) -> ConsumedBoundIntegrationOAuthTransaction:
        now = datetime.now(UTC)
        row = (
            await db.execute(
                update(IntegrationOAuthTransaction)
                .where(
                    IntegrationOAuthTransaction.user_id == actor_id,
                    IntegrationOAuthTransaction.provider == provider,
                    IntegrationOAuthTransaction.state_hash == hash_token(state),
                    IntegrationOAuthTransaction.consumed_at.is_(None),
                    IntegrationOAuthTransaction.expires_at > now,
                )
                .values(consumed_at=now, updated_by=actor_id)
                .returning(
                    IntegrationOAuthTransaction.workspace_id,
                    IntegrationOAuthTransaction.user_id,
                    IntegrationOAuthTransaction.provider,
                    IntegrationOAuthTransaction.purpose,
                    IntegrationOAuthTransaction.subject_type,
                    IntegrationOAuthTransaction.subject_id,
                    IntegrationOAuthTransaction.code_verifier_ciphertext,
                    IntegrationOAuthTransaction.return_path,
                    IntegrationOAuthTransaction.redirect_uri,
                )
            )
        ).one_or_none()
        if row is None:
            await db.rollback()
            raise BadRequestError("Invalid or expired OAuth state.", code="invalid_oauth_state")
        await db.commit()
        try:
            verifier = self._fernet.decrypt(row.code_verifier_ciphertext.encode("ascii")).decode()
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise BadRequestError("Invalid OAuth transaction.", code="invalid_oauth_state") from exc
        return ConsumedBoundIntegrationOAuthTransaction(
            binding=IntegrationOAuthTransactionBinding(
                workspace_id=row.workspace_id,
                actor_id=row.user_id,
                provider=row.provider,
                purpose=row.purpose,
                subject_type=row.subject_type,
                subject_id=row.subject_id,
            ),
            code_verifier=verifier,
            return_path=row.return_path,
            redirect_uri=row.redirect_uri,
        )


__all__ = [
    "ConsumedBoundIntegrationOAuthTransaction",
    "ConsumedIntegrationOAuthTransaction",
    "CreatedIntegrationOAuthTransaction",
    "IntegrationOAuthTransactionBinding",
    "IntegrationOAuthTransactionService",
]
