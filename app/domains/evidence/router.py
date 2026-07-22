from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select

from app.api.deps import (
    DbDep,
    TokenDep,
    get_browser_session,
    get_current_user,
    require_cookie_auth_csrf,
)
from app.domains.evidence.models import EvidenceItem, EvidenceReview
from app.domains.evidence.repository import (
    get_evidence,
    get_latest_review,
    list_claim_rows,
    list_evidence,
)
from app.domains.evidence.schemas import (
    ClaimListResponse,
    ClaimResponse,
    EvidenceCreate,
    EvidenceListResponse,
    EvidenceProvenanceResponse,
    EvidenceRejectRequest,
    EvidenceResponse,
    EvidenceReviewRequest,
    EvidenceReviewResponse,
    LinkedEvidenceResponse,
)
from app.domains.evidence.service import create_evidence, review_evidence
from app.domains.users.models import User
from app.domains.workspaces.models import WorkspaceMembership, WorkspaceRole
from app.shared.exceptions import NotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["evidence"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_evidence_mutation_user(request: Request, token: TokenDep, db: DbDep) -> User:
    if token is not None:
        return await get_current_user(request, token, db)
    return (await require_cookie_auth_csrf(request, await get_browser_session(request, db)))[0]


EvidenceMutationUserDep = Annotated[User, Depends(get_evidence_mutation_user)]


def _freshness(item: EvidenceItem) -> str:
    now = datetime.now(UTC)
    expires_at = item.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            return "expired"
    if item.status in {"stale", "contradicted"}:
        return "stale"
    return "current"


def _review_response(review: EvidenceReview | None) -> EvidenceReviewResponse | None:
    return EvidenceReviewResponse.model_validate(review) if review is not None else None


def _evidence_response(
    item: EvidenceItem, review: EvidenceReview | None = None, *, redact: bool = False
) -> EvidenceResponse:
    provenance = EvidenceProvenanceResponse(
        source_type=item.source_type,
        source_reference=None if redact else item.source_reference,
        source_url=None if redact else item.source_url,
        observed_at=item.observed_at,
    )
    return EvidenceResponse(
        id=item.id,
        workspace_id=item.workspace_id,
        product_id=item.product_id,
        evidence_type=item.evidence_type,
        title="Restricted evidence" if redact else item.title,
        content=None if redact else item.content,
        public_safe_summary=item.public_safe_summary,
        what_happened=None if redact else item.what_happened,
        why_it_matters=None if redact else item.why_it_matters,
        confidence=item.confidence,
        classification=item.classification,
        status=item.status,
        freshness_status=_freshness(item),
        provenance=provenance,
        last_verified_at=item.last_verified_at,
        expires_at=item.expires_at,
        latest_review=None if redact else _review_response(review),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


async def _should_redact_for_user(db: DbDep, workspace_id: UUID, user_id: UUID) -> bool:
    role = await db.scalar(
        select(WorkspaceMembership.role).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.is_active.is_(True),
        )
    )
    return role == WorkspaceRole.viewer


@router.get("/evidence", response_model=EvidenceListResponse)
async def list_evidence_endpoint(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> EvidenceListResponse:
    items = await list_evidence(db, workspace_id, current_user.id)
    if items is None:
        raise NotFoundError("Evidence workspace not found.", code="evidence_workspace_not_found")
    redact_sensitive = await _should_redact_for_user(db, workspace_id, current_user.id)
    responses = []
    for item in items:
        review = await get_latest_review(db, workspace_id, item.id)
        responses.append(
            _evidence_response(
                item,
                review,
                redact=redact_sensitive and item.classification in {"confidential", "blocked"},
            )
        )
    return EvidenceListResponse(items=responses)


@router.post("/evidence", response_model=EvidenceResponse, status_code=status.HTTP_201_CREATED)
async def create_evidence_endpoint(
    workspace_id: UUID,
    payload: EvidenceCreate,
    current_user: EvidenceMutationUserDep,
    db: DbDep,
) -> EvidenceResponse:
    item = await create_evidence(db, workspace_id, current_user.id, payload)
    return _evidence_response(item)


@router.get("/evidence/{evidence_id}", response_model=EvidenceResponse)
async def get_evidence_endpoint(
    workspace_id: UUID, evidence_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> EvidenceResponse:
    item = await get_evidence(db, workspace_id, evidence_id, current_user.id)
    if item is None:
        raise NotFoundError("Evidence not found.", code="evidence_not_found")
    review = await get_latest_review(db, workspace_id, evidence_id)
    redact_sensitive = await _should_redact_for_user(db, workspace_id, current_user.id)
    return _evidence_response(
        item,
        review,
        redact=redact_sensitive and item.classification in {"confidential", "blocked"},
    )


async def _review_endpoint(
    workspace_id: UUID,
    evidence_id: UUID,
    payload: EvidenceReviewRequest,
    current_user: User,
    db: DbDep,
    decision: str,
) -> EvidenceResponse:
    item, review = await review_evidence(
        db, workspace_id, evidence_id, current_user.id, decision, payload.reason
    )
    return _evidence_response(item, review)


@router.post("/evidence/{evidence_id}/approve", response_model=EvidenceResponse)
async def approve_evidence_endpoint(
    workspace_id: UUID,
    evidence_id: UUID,
    payload: EvidenceReviewRequest,
    current_user: EvidenceMutationUserDep,
    db: DbDep,
) -> EvidenceResponse:
    return await _review_endpoint(workspace_id, evidence_id, payload, current_user, db, "approved")


@router.post("/evidence/{evidence_id}/reject", response_model=EvidenceResponse)
async def reject_evidence_endpoint(
    workspace_id: UUID,
    evidence_id: UUID,
    payload: EvidenceRejectRequest,
    current_user: EvidenceMutationUserDep,
    db: DbDep,
) -> EvidenceResponse:
    return await _review_endpoint(
        workspace_id,
        evidence_id,
        EvidenceReviewRequest(reason=payload.reason),
        current_user,
        db,
        "rejected",
    )


@router.get("/claims", response_model=ClaimListResponse)
async def list_claims_endpoint(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> ClaimListResponse:
    rows = await list_claim_rows(db, workspace_id, current_user.id)
    if rows is None:
        raise NotFoundError("Claims workspace not found.", code="claims_workspace_not_found")
    return ClaimListResponse(
        items=[
            ClaimResponse(
                id=claim.id,
                workspace_id=claim.workspace_id,
                product_id=claim.product_id,
                statement=claim.statement,
                status=claim.status,
                last_verified_at=claim.last_verified_at,
                expires_at=claim.expires_at,
                linked_evidence=[LinkedEvidenceResponse.model_validate(link) for link in links],
                created_at=claim.created_at,
                updated_at=claim.updated_at,
            )
            for claim, links in rows
        ]
    )
