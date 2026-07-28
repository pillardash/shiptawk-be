import hashlib
import hmac

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import DbDep
from app.core.config import get_settings
from app.services.github_webhook import (
    SUPPORTED_GITHUB_EVENTS,
    GitHubWebhookEnvelopeError,
    ingest_github_webhook,
    parse_github_webhook_envelope,
)
from app.shared.exceptions import BadRequestError, ServiceUnavailableError, UnauthorizedError
from app.shared.schemas import ApiSchema
from app.workflows.publisher import publish_pending_consumers

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class GitHubWebhookResponse(ApiSchema):
    accepted: bool
    ignored: bool
    duplicate: bool
    consumer_count: int


@router.post(
    "/github",
    response_model=GitHubWebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_github_webhook(request: Request, db: DbDep) -> JSONResponse:
    settings = get_settings()
    if (
        not settings.github_webhook_enabled
        or settings.github_webhook_secret is None
        or settings.github_webhook_payload_encryption_key is None
    ):
        raise ServiceUnavailableError(
            "GitHub webhook ingestion is not configured.", code="github_webhook_unavailable"
        )

    raw_body = await request.body()
    if len(raw_body) > settings.github_webhook_max_body_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Request body is too large.",
        )
    supplied_signature = request.headers.get("X-Hub-Signature-256", "")
    expected_signature = (
        "sha256="
        + hmac.new(
            settings.github_webhook_secret.get_secret_value().encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    )
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise UnauthorizedError(
            "GitHub webhook signature is invalid.", code="invalid_github_webhook_signature"
        )

    try:
        envelope = parse_github_webhook_envelope(
            raw_body,
            delivery_id=request.headers.get("X-GitHub-Delivery"),
            event_name=request.headers.get("X-GitHub-Event"),
        )
    except GitHubWebhookEnvelopeError as exc:
        raise BadRequestError(str(exc), code="invalid_github_webhook") from exc

    if envelope.event_name not in SUPPORTED_GITHUB_EVENTS:
        response = GitHubWebhookResponse(
            accepted=True,
            ignored=True,
            duplicate=False,
            consumer_count=0,
        )
    else:
        result = await ingest_github_webhook(
            db,
            envelope=envelope,
            raw_body=raw_body,
            encryption_key=settings.github_webhook_payload_encryption_key.get_secret_value(),
        )
        if settings.inngest_enabled:
            publisher = request.app.state.workflow_event_publisher
            if publisher is None:
                raise ServiceUnavailableError(
                    "Workflow event publisher is unavailable.",
                    code="workflow_event_publisher_unavailable",
                )
            await publish_pending_consumers(
                db,
                publisher=publisher,
                raw_event_id=result.raw_event_id,
            )
        response = GitHubWebhookResponse(
            accepted=True,
            ignored=False,
            duplicate=result.duplicate,
            consumer_count=result.consumer_count,
        )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=response.model_dump(by_alias=True),
    )
