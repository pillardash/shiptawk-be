from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.products.enums.product_profile_enum import (
    ProductProfileAuditAction,
    ProductProfileStatus,
)
from app.modules.products.models import ProductProfile, ProductProfileAuditEvent
from app.modules.products.policies.product_profile_policy import (
    can_approve,
    can_write,
    changed_fields,
    completeness,
)
from app.modules.products.repositories.product_profile_repository import (
    get_product_profile_state,
    get_profile_for_update,
    get_profile_version,
    list_audit_events,
    list_versions,
)
from app.modules.products.repositories.product_repository import (
    get_product_for_update,
    get_product_for_workspace,
)
from app.modules.products.schemas.product_profile_schema import (
    ApprovedProductProfileContext,
    ProductProfileDraftUpdate,
)
from app.modules.workspaces.services.access import require_active_workspace_role
from app.shared.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)

CONTENT_FIELDS = (
    "product_name",
    "short_description",
    "primary_audience",
    "primary_customer_problem",
    "main_value_proposition",
    "primary_conversion_goal",
    "primary_conversion_url",
    "main_market",
    "differentiation",
    "important_capabilities",
    "quarterly_objective",
    "restricted_topics",
    "restricted_claims",
    "restricted_language",
    "brand_voice_guidance",
)


def _values(payload: ProductProfileDraftUpdate) -> dict[str, object]:
    data = payload.model_dump(exclude={"expected_revision"})
    return {
        field: (value or None) if isinstance(value, str) else value for field, value in data.items()
    }


def _profile_values(profile: ProductProfile) -> dict[str, object]:
    return {field: getattr(profile, field) for field in CONTENT_FIELDS}


async def _access(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, *, approve: bool = False
) -> None:
    role = await require_active_workspace_role(
        db,
        workspace_id,
        user_id,
        missing=NotFoundError("Product profile not found.", code="product_profile_not_found"),
    )
    if not (can_approve(role) if approve else can_write(role)):
        raise ForbiddenError(
            "You do not have permission to modify this product profile.",
            code="product_profile_forbidden",
        )


async def _commit_profile_mutation(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise ConflictError(
            "The product profile changed. Reload and try again.",
            code="product_profile_revision_conflict",
        ) from error
    except Exception:
        await db.rollback()
        raise


async def read_product_profile(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID
) -> tuple[ProductProfile | None, ProductProfile | None]:
    if await get_product_for_workspace(db, workspace_id, product_id, user_id) is None:
        raise NotFoundError("Product profile not found.", code="product_profile_not_found")
    return await get_product_profile_state(db, workspace_id, product_id)


async def save_draft(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    payload: ProductProfileDraftUpdate,
) -> ProductProfile:
    await _access(db, workspace_id, user_id)
    if await get_product_for_update(db, workspace_id, product_id) is None:
        raise NotFoundError("Product profile not found.", code="product_profile_not_found")
    draft = await get_profile_for_update(db, workspace_id, product_id, ProductProfileStatus.draft)
    approved = await get_profile_for_update(
        db, workspace_id, product_id, ProductProfileStatus.approved
    )
    if draft is None:
        if payload.expected_revision is not None:
            raise ConflictError(
                "The product profile changed. Reload and try again.",
                code="product_profile_revision_conflict",
            )
        draft = ProductProfile(
            workspace_id=workspace_id,
            product_id=product_id,
            status=ProductProfileStatus.draft,
            draft_revision=1,
            schema_version=1,
            important_capabilities=[],
            restricted_topics=[],
            restricted_claims=[],
            restricted_language=[],
            completeness_score=0,
            based_on_profile_id=approved.id if approved else None,
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(draft)
        before = None
    else:
        before = _profile_values(draft)
        if payload.expected_revision != draft.draft_revision:
            raise ConflictError(
                "The product profile changed. Reload and try again.",
                code="product_profile_revision_conflict",
            )
    values = _values(payload)
    for field, value in values.items():
        setattr(draft, field, value)
    if before is not None:
        draft.draft_revision += 1
    draft.completeness_score, _ = completeness(_profile_values(draft))
    draft.updated_by = user_id
    await db.flush()
    db.add(
        ProductProfileAuditEvent(
            workspace_id=workspace_id,
            product_id=product_id,
            profile_id=draft.id,
            actor_id=user_id,
            action=(
                ProductProfileAuditAction.draft_created
                if before is None
                else ProductProfileAuditAction.draft_updated
            ),
            draft_revision=draft.draft_revision,
            changed_fields=changed_fields(before, _profile_values(draft)),
        )
    )
    await _commit_profile_mutation(db)
    await db.refresh(draft)
    return draft


async def approve_draft(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, user_id: UUID, expected_revision: int
) -> ProductProfile:
    await _access(db, workspace_id, user_id, approve=True)
    if await get_product_for_update(db, workspace_id, product_id) is None:
        raise NotFoundError("Product profile not found.", code="product_profile_not_found")
    draft = await get_profile_for_update(db, workspace_id, product_id, ProductProfileStatus.draft)
    if draft is None:
        raise NotFoundError(
            "Product profile draft not found.", code="product_profile_draft_not_found"
        )
    if draft.draft_revision != expected_revision:
        raise ConflictError(
            "The product profile changed. Reload and try again.",
            code="product_profile_revision_conflict",
        )
    score, missing = completeness(_profile_values(draft))
    draft.completeness_score = score
    if missing:
        raise UnprocessableEntityError(
            f"Product profile is incomplete: {', '.join(missing)}.",
            code="product_profile_incomplete",
        )
    current = await get_profile_for_update(
        db, workspace_id, product_id, ProductProfileStatus.approved
    )
    next_version = (current.version if current and current.version else 0) + 1
    if current:
        current.status = ProductProfileStatus.superseded
        await db.flush()
    draft.status = ProductProfileStatus.approved
    draft.version = next_version
    draft.approved_at = datetime.now(UTC)
    draft.approved_by = user_id
    draft.updated_by = user_id
    await db.flush()
    db.add(
        ProductProfileAuditEvent(
            workspace_id=workspace_id,
            product_id=product_id,
            profile_id=draft.id,
            actor_id=user_id,
            action=ProductProfileAuditAction.profile_approved,
            profile_version=next_version,
            draft_revision=draft.draft_revision,
            changed_fields=[],
        )
    )
    if current:
        db.add(
            ProductProfileAuditEvent(
                workspace_id=workspace_id,
                product_id=product_id,
                profile_id=current.id,
                actor_id=user_id,
                action=ProductProfileAuditAction.profile_superseded,
                profile_version=current.version,
                draft_revision=current.draft_revision,
                changed_fields=[],
            )
        )
    await _commit_profile_mutation(db)
    await db.refresh(draft)
    return draft


async def profile_versions(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[ProductProfile]:
    await read_product_profile(db, workspace_id, product_id, user_id)
    return await list_versions(db, workspace_id, product_id, limit=limit, offset=offset)


async def profile_version(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    version: int,
) -> ProductProfile:
    await read_product_profile(db, workspace_id, product_id, user_id)
    profile = await get_profile_version(db, workspace_id, product_id, version)
    if profile is None:
        raise NotFoundError(
            "Product profile version not found.", code="product_profile_version_not_found"
        )
    return profile


async def profile_audit_events(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    user_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[ProductProfileAuditEvent]:
    await read_product_profile(db, workspace_id, product_id, user_id)
    return await list_audit_events(db, workspace_id, product_id, limit=limit, offset=offset)


async def resolve_approved_context(
    db: AsyncSession, workspace_id: UUID, product_id: UUID
) -> ApprovedProductProfileContext:
    _, approved = await get_product_profile_state(db, workspace_id, product_id)
    if approved is None or approved.version is None:
        raise NotFoundError(
            "An approved product profile is required.", code="approved_product_profile_required"
        )
    return ApprovedProductProfileContext(
        schema_version=1,
        profile_id=approved.id,
        profile_version=approved.version,
        product_id=product_id,
        **_profile_values(approved),
    )
