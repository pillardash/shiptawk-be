from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from app.api.deps import (
    DbDep,
    LLMExecutionServiceDep,
    MutationUserDep,
    get_current_user,
)
from app.modules.drafts.enums import DraftStatus
from app.modules.drafts.providers.protocols import CredentialDecryptor, Publisher
from app.modules.drafts.repositories.drafts import (
    get_draft_review_for_workspace,
    list_drafts_for_workspace,
)
from app.modules.drafts.schemas import (
    DraftAngleRegeneration,
    DraftApproveAndPublishCommand,
    DraftApproveAndPublishResponse,
    DraftCandidateSelection,
    DraftContentUpdate,
    DraftListResponse,
    DraftPublishResponse,
    DraftRejection,
    DraftResponse,
    DraftReviewData,
    DraftReviewResponse,
    EventEvaluationResponse,
    PaginationResponse,
    TweetCandidateResponse,
)
from app.modules.drafts.services.regeneration import regenerate_draft_angle
from app.modules.drafts.services.review import (
    approve_and_publish_draft_revision,
    approve_draft,
    edit_draft_content,
    publish_draft,
    reject_draft,
    select_draft_candidate,
)
from app.modules.identity.models.users import User
from app.shared.exceptions import NotFoundError, ServiceUnavailableError

router = APIRouter(prefix="/workspaces/{workspace_id}/drafts", tags=["drafts"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_publisher(request: Request) -> Publisher:
    publisher = getattr(request.app.state, "publisher", None)
    if publisher is None:
        raise ServiceUnavailableError(
            "Publishing provider is not configured.", code="publisher_not_configured"
        )
    return cast(Publisher, publisher)


def get_credential_decryptor(request: Request) -> CredentialDecryptor:
    decryptor = getattr(request.app.state, "integration_credential_decryptor", None)
    if decryptor is None:
        raise ServiceUnavailableError(
            "Integration credential decryption is not configured.",
            code="integration_credentials_not_configured",
        )
    return cast(CredentialDecryptor, decryptor)


PublisherDep = Annotated[Publisher, Depends(get_publisher)]
CredentialDecryptorDep = Annotated[CredentialDecryptor, Depends(get_credential_decryptor)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


@router.get("", response_model=DraftListResponse)
async def list_drafts(
    workspace_id: UUID,
    current_user: CurrentUserDep,
    db: DbDep,
    status: DraftStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DraftListResponse:
    result = await list_drafts_for_workspace(db, workspace_id, current_user.id, status, page, limit)
    if result is None:
        raise NotFoundError("Draft workspace not found.", code="draft_workspace_not_found")

    rows, total = result
    total_pages = max(1, (total + limit - 1) // limit)
    return DraftListResponse(
        data=[DraftResponse.model_validate(row) for row in rows],
        pagination=PaginationResponse(
            page=page,
            limit=limit,
            total=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.get("/{draft_id}/review", response_model=DraftReviewResponse)
async def get_draft_review(
    workspace_id: UUID, draft_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> DraftReviewResponse:
    result = await get_draft_review_for_workspace(db, workspace_id, draft_id, current_user.id)
    if result is None:
        raise NotFoundError("Draft not found.", code="draft_not_found")

    draft, evaluation, candidates = result
    return DraftReviewResponse(
        data=DraftReviewData(
            draft=DraftResponse.model_validate(draft),
            evaluation=(
                EventEvaluationResponse.model_validate(evaluation)
                if evaluation is not None
                else None
            ),
            candidates=[
                TweetCandidateResponse.model_validate(candidate) for candidate in candidates
            ],
        )
    )


@router.patch("/{draft_id}/content", response_model=DraftResponse)
async def edit_draft_content_endpoint(
    workspace_id: UUID,
    draft_id: UUID,
    payload: DraftContentUpdate,
    current_user: MutationUserDep,
    db: DbDep,
) -> DraftResponse:
    draft = await edit_draft_content(db, workspace_id, draft_id, current_user.id, payload.content)
    return DraftResponse.model_validate(draft)


@router.post("/{draft_id}/approve", response_model=DraftResponse)
async def approve_draft_endpoint(
    workspace_id: UUID,
    draft_id: UUID,
    current_user: MutationUserDep,
    db: DbDep,
) -> DraftResponse:
    draft = await approve_draft(db, workspace_id, draft_id, current_user.id)
    return DraftResponse.model_validate(draft)


@router.post("/{draft_id}/reject", response_model=DraftResponse)
async def reject_draft_endpoint(
    workspace_id: UUID,
    draft_id: UUID,
    payload: DraftRejection,
    current_user: MutationUserDep,
    db: DbDep,
) -> DraftResponse:
    draft = await reject_draft(db, workspace_id, draft_id, current_user.id, payload.reason)
    return DraftResponse.model_validate(draft)


@router.post("/{draft_id}/select-candidate", response_model=DraftResponse)
async def select_draft_candidate_endpoint(
    workspace_id: UUID,
    draft_id: UUID,
    payload: DraftCandidateSelection,
    current_user: MutationUserDep,
    db: DbDep,
) -> DraftResponse:
    draft = await select_draft_candidate(
        db, workspace_id, draft_id, current_user.id, payload.candidate_id
    )
    return DraftResponse.model_validate(draft)


@router.post("/{draft_id}/regenerate-angle", response_model=DraftResponse)
async def regenerate_draft_angle_endpoint(
    workspace_id: UUID,
    draft_id: UUID,
    payload: DraftAngleRegeneration,
    current_user: MutationUserDep,
    db: DbDep,
    llm: LLMExecutionServiceDep,
) -> DraftResponse:
    draft = await regenerate_draft_angle(db, workspace_id, draft_id, current_user.id, payload, llm)
    return DraftResponse.model_validate(draft)


@router.post("/{draft_id}/publish", response_model=DraftPublishResponse)
async def publish_draft_endpoint(
    workspace_id: UUID,
    draft_id: UUID,
    current_user: MutationUserDep,
    db: DbDep,
    publisher: PublisherDep,
    credential_decryptor: CredentialDecryptorDep,
) -> DraftPublishResponse:
    receipt = await publish_draft(
        db,
        workspace_id,
        draft_id,
        current_user.id,
        publisher,
        credential_decryptor,
    )
    return DraftPublishResponse.model_validate(receipt)


@router.post(
    "/{draft_id}/approve-and-publish",
    response_model=DraftApproveAndPublishResponse,
)
async def approve_and_publish_draft_revision_endpoint(
    workspace_id: UUID,
    draft_id: UUID,
    payload: DraftApproveAndPublishCommand,
    idempotency_key: IdempotencyKey,
    current_user: MutationUserDep,
    db: DbDep,
    publisher: PublisherDep,
    credential_decryptor: CredentialDecryptorDep,
) -> DraftApproveAndPublishResponse:
    result = await approve_and_publish_draft_revision(
        db,
        workspace_id=workspace_id,
        draft_id=draft_id,
        asset_id=payload.asset_id,
        revision=payload.revision,
        actor_id=current_user.id,
        idempotency_key=idempotency_key,
        publisher=publisher,
        credential_decryptor=credential_decryptor,
    )
    return DraftApproveAndPublishResponse.model_validate(result)
