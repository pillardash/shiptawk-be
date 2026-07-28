from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.drafts.models import draft_status_events, drafts
from app.modules.operator.models import ActionRiskReview, PreparedAsset
from app.modules.products.models import Product, product_repositories


async def project_x_asset_to_draft(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    asset_id: UUID,
    revision: int,
) -> UUID | None:
    """Bridge an approved exact asset revision into the sole X publishing aggregate."""
    asset = await db.scalar(
        select(PreparedAsset).where(
            PreparedAsset.workspace_id == workspace_id,
            PreparedAsset.product_id == product_id,
            PreparedAsset.id == asset_id,
            PreparedAsset.revision == revision,
            PreparedAsset.kind == "x_draft",
        )
    )
    if asset is None:
        return None
    review = await db.scalar(
        select(ActionRiskReview).where(
            ActionRiskReview.workspace_id == workspace_id,
            ActionRiskReview.product_id == product_id,
            ActionRiskReview.asset_id == asset_id,
            ActionRiskReview.asset_revision == revision,
            ActionRiskReview.final_outcome != "block",
        )
    )
    if review is None:
        return None
    existing = await db.scalar(
        select(drafts.c.id).where(
            drafts.c.workspace_id == workspace_id,
            drafts.c.prepared_asset_id == asset_id,
            drafts.c.prepared_asset_revision == revision,
        )
    )
    if existing is not None:
        return cast(UUID, existing)
    product = await db.scalar(
        select(Product).where(Product.workspace_id == workspace_id, Product.id == product_id)
    )
    repo_id = await db.scalar(
        select(product_repositories.c.repo_id)
        .where(
            product_repositories.c.workspace_id == workspace_id,
            product_repositories.c.product_id == product_id,
        )
        .order_by(product_repositories.c.is_primary_repo.desc(), product_repositories.c.id)
        .limit(1)
    )
    if product is None or repo_id is None:
        return None
    raw_content = asset.structured_content.get("content", "")
    content = raw_content if isinstance(raw_content, str) else str(raw_content)
    draft_id = uuid4()
    now = datetime.now(UTC)
    await db.execute(
        insert(drafts).values(
            id=draft_id,
            workspace_id=workspace_id,
            user_id=product.user_id,
            repo_id=repo_id,
            content=content,
            source_event_type="operator_x_asset",
            source_event_payload={"productId": str(product_id)},
            status="approved",
            approved_at=now,
            prepared_asset_id=asset_id,
            prepared_asset_revision=revision,
        )
    )
    await db.execute(
        insert(draft_status_events).values(
            id=uuid4(),
            workspace_id=workspace_id,
            user_id=product.user_id,
            repo_id=repo_id,
            draft_id=draft_id,
            status="approved",
            occurred_at=now,
        )
    )
    return draft_id
