import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.idempotency import insert_or_replay
from app.modules.operator.enums import DismissalReason
from app.modules.operator.models import (
    OpportunityEvaluation,
    OpportunityFeedback,
    OpportunityStatusEvent,
)
from app.modules.operator.policies.cooldown_policy import DISMISSAL_DAYS
from app.modules.operator.policies.opportunity_transition_policy import require_transition
from app.modules.operator.repositories import opportunity_feedback_repository as feedback_repository
from app.modules.operator.repositories import opportunity_repository
from app.modules.operator.schemas import OpportunityFeedbackCommand


class FeedbackIdempotencyConflictError(ValueError):
    pass


class OpportunityNotFoundError(LookupError):
    pass


def _reason(command: OpportunityFeedbackCommand) -> str:
    value = command.reason_code
    return value.value if hasattr(value, "value") else str(value)


def _fingerprint(command: OpportunityFeedbackCommand, action: str) -> str:
    payload = {"action": action, **command.model_dump(mode="json", exclude_none=False)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _replay(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, key: str, fingerprint: str
) -> OpportunityFeedback | None:
    existing = await feedback_repository.get_by_idempotency_key(db, workspace_id, product_id, key)
    if existing is not None and existing.request_fingerprint != fingerprint:
        raise FeedbackIdempotencyConflictError(
            "Idempotency key was already used with another body."
        )
    return existing


async def _validated_evaluation_id(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    opportunity_id: UUID,
    evaluation_id: UUID | None,
) -> UUID | None:
    if evaluation_id is None:
        return None
    valid = await db.scalar(
        select(OpportunityEvaluation.id).where(
            OpportunityEvaluation.workspace_id == workspace_id,
            OpportunityEvaluation.product_id == product_id,
            OpportunityEvaluation.id == evaluation_id,
            OpportunityEvaluation.accepted_opportunity_id == opportunity_id,
        )
    )
    if valid is None:
        raise ValueError("Evaluation does not belong to the opportunity.")
    return valid


async def _insert_feedback(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    opportunity_id: UUID,
    evaluation_id: UUID | None,
    actor_id: UUID,
    idempotency_key: str,
    fingerprint: str,
    command: OpportunityFeedbackCommand,
) -> tuple[OpportunityFeedback, bool]:
    result = await insert_or_replay(
        db,
        cast(Table, OpportunityFeedback.__table__),
        values={
            "id": uuid4(),
            "workspace_id": workspace_id,
            "product_id": product_id,
            "opportunity_id": opportunity_id,
            "evaluation_id": evaluation_id,
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
            "request_fingerprint": fingerprint,
            "usefulness": command.usefulness,
            "reason_code": _reason(command),
            "comment": command.comment,
            "evaluation_eligible": command.evaluation_eligible,
            "created_at": datetime.now(UTC),
        },
        conflict_columns=("workspace_id", "product_id", "idempotency_key"),
    )
    if result.value["request_fingerprint"] != fingerprint:
        raise FeedbackIdempotencyConflictError(
            "Idempotency key was already used with another body."
        )
    feedback = await db.get(OpportunityFeedback, result.value["id"], populate_existing=True)
    assert feedback is not None
    return feedback, result.inserted


async def append_feedback(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    idempotency_key: str,
    command: OpportunityFeedbackCommand,
) -> OpportunityFeedback:
    if not idempotency_key.strip() or len(idempotency_key) > 255:
        raise ValueError("Idempotency-Key must contain between 1 and 255 characters.")
    fingerprint = _fingerprint(command, "feedback")
    try:
        existing = await _replay(db, workspace_id, product_id, idempotency_key, fingerprint)
        if existing is not None:
            return existing
        opportunity = await opportunity_repository.get(
            db, workspace_id, product_id, command.opportunity_id
        )
        if opportunity is None:
            raise OpportunityNotFoundError("Opportunity was not found.")
        evaluation_id = await _validated_evaluation_id(
            db, workspace_id, product_id, opportunity.id, command.evaluation_id
        )
        feedback, _ = await _insert_feedback(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            opportunity_id=opportunity.id,
            evaluation_id=evaluation_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            command=command,
        )
        await db.commit()
        return feedback
    except Exception:
        await db.rollback()
        raise


async def dismiss_opportunity(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    idempotency_key: str,
    command: OpportunityFeedbackCommand,
) -> OpportunityFeedback:
    if not idempotency_key.strip() or len(idempotency_key) > 255:
        raise ValueError("Idempotency-Key must contain between 1 and 255 characters.")
    fingerprint = _fingerprint(command, "dismiss")
    try:
        opportunity = await opportunity_repository.get(
            db, workspace_id, product_id, command.opportunity_id, for_update=True
        )
        if opportunity is None:
            raise OpportunityNotFoundError("Opportunity was not found.")
        existing = await _replay(db, workspace_id, product_id, idempotency_key, fingerprint)
        if existing is not None:
            return existing
        require_transition(opportunity.status, "dismissed", internal=False)
        reason = _reason(command)
        try:
            DismissalReason(reason)
        except ValueError as exc:
            raise ValueError("Reason is not valid for dismissal.") from exc
        evaluation_id = await _validated_evaluation_id(
            db, workspace_id, product_id, opportunity.id, command.evaluation_id
        )
        opportunity.status = "dismissed"
        opportunity.cooldown_until = datetime.now(UTC) + timedelta(days=DISMISSAL_DAYS[reason])
        feedback, inserted = await _insert_feedback(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            opportunity_id=opportunity.id,
            evaluation_id=evaluation_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            command=command,
        )
        if inserted:
            await feedback_repository.add_status_event(
                db,
                OpportunityStatusEvent(
                    workspace_id=workspace_id,
                    product_id=product_id,
                    opportunity_id=opportunity.id,
                    actor_id=actor_id,
                    previous_status="open",
                    new_status="dismissed",
                    reason_code=reason,
                    feedback_id=feedback.id,
                    created_at=datetime.now(UTC),
                ),
            )
        await db.commit()
        return feedback
    except Exception:
        await db.rollback()
        raise


async def supersede_opportunity(
    db: AsyncSession, *, workspace_id: UUID, product_id: UUID, opportunity_id: UUID, actor_id: UUID
) -> None:
    try:
        opportunity = await opportunity_repository.get(
            db, workspace_id, product_id, opportunity_id, for_update=True
        )
        if opportunity is None:
            raise OpportunityNotFoundError("Opportunity was not found.")
        previous = opportunity.status
        require_transition(previous, "superseded", internal=True)
        opportunity.status = "superseded"
        await feedback_repository.add_status_event(
            db,
            OpportunityStatusEvent(
                workspace_id=workspace_id,
                product_id=product_id,
                opportunity_id=opportunity.id,
                actor_id=actor_id,
                previous_status=previous,
                new_status="superseded",
                created_at=datetime.now(UTC),
            ),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
