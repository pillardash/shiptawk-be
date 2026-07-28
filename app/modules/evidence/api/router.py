from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import (
    DbDep,
    MutationUserDep,
    get_current_user,
)
from app.modules.evidence.models import EvidenceItem, EvidenceReview
from app.modules.evidence.policies.access import should_redact_evidence
from app.modules.evidence.policies.freshness import evidence_freshness
from app.modules.evidence.repositories.evidence import (
    get_evidence,
    get_latest_review,
    list_claim_rows,
    list_evidence,
)
from app.modules.evidence.schemas import (
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
from app.modules.evidence.services.evidence import create_evidence, review_evidence
from app.modules.identity.models.users import User
from app.modules.workspaces.services import resolve_active_workspace_role
from app.shared.exceptions import NotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["evidence"])
CurrentUserDep = Annotated[User, Depends(get_current_user)]


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
        freshness_status=evidence_freshness(item),
        provenance=provenance,
        last_verified_at=item.last_verified_at,
        expires_at=item.expires_at,
        latest_review=None if redact else _review_response(review),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get("/evidence", response_model=EvidenceListResponse)
async def list_evidence_endpoint(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> EvidenceListResponse:
    items = await list_evidence(db, workspace_id, current_user.id)
    if items is None:
        raise NotFoundError("Evidence workspace not found.", code="evidence_workspace_not_found")
    role = await resolve_active_workspace_role(db, workspace_id, current_user.id)
    responses = []
    for item in items:
        review = await get_latest_review(db, workspace_id, item.id)
        responses.append(
            _evidence_response(
                item,
                review,
                redact=should_redact_evidence(role, item.classification),
            )
        )
    return EvidenceListResponse(items=responses)


@router.post("/evidence", response_model=EvidenceResponse, status_code=status.HTTP_201_CREATED)
async def create_evidence_endpoint(
    workspace_id: UUID,
    payload: EvidenceCreate,
    current_user: MutationUserDep,
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
    role = await resolve_active_workspace_role(db, workspace_id, current_user.id)
    return _evidence_response(
        item,
        review,
        redact=should_redact_evidence(role, item.classification),
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
    current_user: MutationUserDep,
    db: DbDep,
) -> EvidenceResponse:
    return await _review_endpoint(workspace_id, evidence_id, payload, current_user, db, "approved")


@router.post("/evidence/{evidence_id}/reject", response_model=EvidenceResponse)
async def reject_evidence_endpoint(
    workspace_id: UUID,
    evidence_id: UUID,
    payload: EvidenceRejectRequest,
    current_user: MutationUserDep,
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
