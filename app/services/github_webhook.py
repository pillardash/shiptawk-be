import json
import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.legacy.models import github_raw_event_consumers, github_raw_events, repos

SUPPORTED_GITHUB_EVENTS = frozenset({"push", "pull_request", "release"})
GITHUB_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PAYLOAD_KEY_VERSION = "fernet-v1"


class GitHubWebhookEnvelopeError(ValueError):
    """The authenticated delivery does not contain a usable GitHub envelope."""


@dataclass(frozen=True, slots=True)
class GitHubWebhookEnvelope:
    delivery_id: str
    event_name: str
    repo_full_name: str


@dataclass(frozen=True, slots=True)
class GitHubWebhookIngestionResult:
    duplicate: bool
    consumer_count: int
    raw_event_id: UUID


def parse_github_webhook_envelope(
    raw_body: bytes, *, delivery_id: str | None, event_name: str | None
) -> GitHubWebhookEnvelope:
    delivery = delivery_id.strip() if delivery_id else ""
    event = event_name.strip() if event_name else ""
    if not delivery or len(delivery) > 255:
        raise GitHubWebhookEnvelopeError("Invalid GitHub delivery identifier.")
    if not event or len(event) > 64 or not re.fullmatch(r"[a-z_]+", event):
        raise GitHubWebhookEnvelopeError("Invalid GitHub event name.")
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubWebhookEnvelopeError("GitHub webhook body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise GitHubWebhookEnvelopeError("GitHub webhook body must be a JSON object.")
    repository = payload.get("repository")
    full_name = repository.get("full_name") if isinstance(repository, dict) else None
    if (
        not isinstance(full_name, str)
        or len(full_name) > 255
        or GITHUB_REPOSITORY_PATTERN.fullmatch(full_name) is None
    ):
        raise GitHubWebhookEnvelopeError("GitHub webhook repository is invalid.")
    return GitHubWebhookEnvelope(
        delivery_id=delivery,
        event_name=event,
        repo_full_name=full_name,
    )


def encrypt_github_webhook_payload(raw_body: bytes, encryption_key: str) -> str:
    return Fernet(encryption_key.encode("ascii")).encrypt(raw_body).decode("ascii")


def decrypt_github_webhook_payload(payload_ciphertext: str, encryption_key: str) -> bytes:
    """Decrypt restricted ingress for an already-authorized internal worker only."""
    try:
        return Fernet(encryption_key.encode("ascii")).decrypt(payload_ciphertext.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("GitHub webhook payload could not be decrypted.") from exc


def _insert_for(db: AsyncSession, table: object) -> object:
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return postgresql_insert(table)  # type: ignore[arg-type]
    if dialect == "sqlite":
        return sqlite_insert(table)  # type: ignore[arg-type]
    raise RuntimeError("GitHub webhook ingestion requires PostgreSQL or SQLite.")


async def ingest_github_webhook(
    db: AsyncSession,
    *,
    envelope: GitHubWebhookEnvelope,
    raw_body: bytes,
    encryption_key: str,
) -> GitHubWebhookIngestionResult:
    raw_event_id = uuid4()
    raw_insert = (
        _insert_for(db, github_raw_events)
        .values(  # type: ignore[attr-defined]
            id=raw_event_id,
            github_delivery_id=envelope.delivery_id,
            event_name=envelope.event_name,
            repo_full_name=envelope.repo_full_name,
            payload_ciphertext=encrypt_github_webhook_payload(raw_body, encryption_key),
            payload_key_version=PAYLOAD_KEY_VERSION,
        )
        .on_conflict_do_nothing(index_elements=["github_delivery_id"])
        .returning(github_raw_events.c.id)
    )
    inserted_id = await db.scalar(raw_insert)
    if inserted_id is None:
        existing_id = await db.scalar(
            select(github_raw_events.c.id).where(
                github_raw_events.c.github_delivery_id == envelope.delivery_id
            )
        )
        if existing_id is None:
            raise RuntimeError("GitHub delivery conflict did not resolve to a stored event.")
        consumer_count = await db.scalar(
            select(func.count())
            .select_from(github_raw_event_consumers)
            .where(github_raw_event_consumers.c.raw_event_id == existing_id)
        )
        await db.commit()
        return GitHubWebhookIngestionResult(
            duplicate=True,
            consumer_count=int(consumer_count or 0),
            raw_event_id=existing_id,
        )

    tracked = (
        await db.execute(
            select(repos.c.workspace_id, repos.c.id, repos.c.user_id).where(
                repos.c.repo_full_name == envelope.repo_full_name,
                repos.c.is_tracked.is_(True),
            )
        )
    ).all()
    if tracked:
        consumer_insert = _insert_for(db, github_raw_event_consumers).values(  # type: ignore[attr-defined]
            [
                {
                    "id": uuid4(),
                    "workspace_id": row.workspace_id,
                    "raw_event_id": raw_event_id,
                    "repo_id": row.id,
                    "user_id": row.user_id,
                    "processing_state": "pending",
                }
                for row in tracked
            ]
        )
        await db.execute(
            consumer_insert.on_conflict_do_nothing(index_elements=["workspace_id", "raw_event_id"])
        )
    await db.commit()
    return GitHubWebhookIngestionResult(
        duplicate=False, consumer_count=len(tracked), raw_event_id=raw_event_id
    )
