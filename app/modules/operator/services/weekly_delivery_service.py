from dataclasses import dataclass
from html import escape
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.identity.models.users import User
from app.modules.operator.models import (
    MarketingPlan,
    PlanAction,
    PreparedAsset,
    WeeklyGrowthDelivery,
)
from app.modules.operator.services.weekly_growth_service import snapshot_hash
from app.modules.products.models import Product, product_repositories
from app.modules.repos.models import repos
from app.services.email.base import EmailAddress, EmailMessage, EmailService


@dataclass(frozen=True, slots=True)
class WeeklyEmailProjection:
    subject: str
    text: str
    html: str
    snapshot_hash: str


async def build_weekly_email_projection(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    plan_id: UUID,
    frontend_url: str,
) -> WeeklyEmailProjection:
    plan = await db.scalar(
        select(MarketingPlan).where(
            MarketingPlan.workspace_id == workspace_id,
            MarketingPlan.product_id == product_id,
            MarketingPlan.id == plan_id,
            MarketingPlan.status == "ready",
        )
    )
    if plan is None or plan.plan_snapshot is None:
        raise LookupError("Finalized weekly plan not found.")
    product = await db.scalar(
        select(Product).where(Product.workspace_id == workspace_id, Product.id == product_id)
    )
    if product is None:
        raise LookupError("Product not found.")
    actions = list(
        await db.scalars(
            select(PlanAction)
            .where(
                PlanAction.workspace_id == workspace_id,
                PlanAction.product_id == product_id,
                PlanAction.plan_id == plan_id,
            )
            .order_by(PlanAction.position, PlanAction.id)
        )
    )
    assets = (
        list(
            await db.scalars(
                select(PreparedAsset).where(
                    PreparedAsset.workspace_id == workspace_id,
                    PreparedAsset.product_id == product_id,
                    PreparedAsset.action_id.in_([action.id for action in actions]),
                )
            )
        )
        if actions
        else []
    )
    repository_rows = (
        await db.execute(
            select(repos.c.repo_full_name, product_repositories.c.repo_role)
            .select_from(
                product_repositories.join(
                    repos,
                    (repos.c.workspace_id == product_repositories.c.workspace_id)
                    & (repos.c.id == product_repositories.c.repo_id),
                )
            )
            .where(
                product_repositories.c.workspace_id == workspace_id,
                product_repositories.c.product_id == product_id,
            )
            .order_by(product_repositories.c.is_primary_repo.desc(), repos.c.repo_full_name)
        )
    ).all()
    snapshot = plan.plan_snapshot
    shipped = snapshot.get("shipped", {})
    search = snapshot.get("search", {})
    lines = [
        f"Weekly growth plan for {product.name}",
        "",
        "What shipped",
        str(shipped),
        "",
        "Search changes",
        str(search),
        "",
        "Best opportunity",
        actions[0].title if actions else "No evidence-backed opportunity this week.",
        "",
        f"Actions ({len(actions)})",
    ]
    lines.extend(f"{action.position}. {action.title}" for action in actions)
    lines.extend(["", "Prepared outputs"])
    lines.extend(
        f"{asset.kind}: {frontend_url}/products/{product_id}/assets/"
        f"{asset.id}?revision={asset.revision}"
        for asset in sorted(assets, key=lambda item: (str(item.action_id), item.revision))
    )
    lines.extend(["", "Repository details"])
    lines.extend(f"{name} ({role})" for name, role in repository_rows)
    text = "\n".join(lines)
    return WeeklyEmailProjection(
        subject=f"{product.name}: weekly growth plan",
        text=text,
        html=f"<pre>{escape(text)}</pre>",
        snapshot_hash=snapshot_hash(plan),
    )


class WeeklyGrowthEmailWorkflow:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        email_service: EmailService,
        frontend_url: str,
    ) -> None:
        self.sessions = sessions
        self.email_service = email_service
        self.frontend_url = frontend_url

    async def deliver(
        self, plan_id: UUID, workspace_id: UUID, product_id: UUID, plan_revision: int
    ) -> dict[str, object]:
        async with self.sessions() as db:
            product = await db.scalar(
                select(Product).where(
                    Product.workspace_id == workspace_id, Product.id == product_id
                )
            )
            if product is None or not (
                product.weekly_growth_operator_enabled and product.operator_email_enabled
            ):
                return {"status": "suppressed", "planId": str(plan_id)}
            recipient = await db.scalar(select(User.email).where(User.id == product.user_id))
            if not recipient:
                return {"status": "suppressed", "planId": str(plan_id)}
            projection = await build_weekly_email_projection(
                db,
                workspace_id=workspace_id,
                product_id=product_id,
                plan_id=plan_id,
                frontend_url=self.frontend_url,
            )
            key = f"weekly-growth:{plan_id}:{plan_revision}:{recipient.casefold()}"
            delivery = await db.scalar(
                select(WeeklyGrowthDelivery).where(
                    WeeklyGrowthDelivery.workspace_id == workspace_id,
                    WeeklyGrowthDelivery.product_id == product_id,
                    WeeklyGrowthDelivery.idempotency_key == key,
                )
            )
            if delivery is not None and delivery.status == "delivered":
                return {"status": "delivered", "deliveryId": str(delivery.id), "duplicate": True}
            if delivery is None:
                delivery = WeeklyGrowthDelivery(
                    workspace_id=workspace_id,
                    product_id=product_id,
                    plan_id=plan_id,
                    plan_revision=plan_revision,
                    plan_hash=projection.snapshot_hash,
                    recipient=recipient,
                    idempotency_key=key,
                    provider="email",
                    status="pending",
                )
                db.add(delivery)
                await db.commit()
            try:
                self.email_service.send(
                    EmailMessage(
                        to=[EmailAddress(recipient)],
                        subject=projection.subject,
                        text=projection.text,
                        html=projection.html,
                    )
                )
            except Exception:
                delivery.status = "failed"
                await db.commit()
                raise
            delivery.status = "delivered"
            delivery.provider_delivery_id = key
            delivery.provider_receipt = {"idempotencyKey": key}
            from datetime import UTC, datetime

            delivery.delivered_at = datetime.now(UTC)
            await db.commit()
            return {"status": "delivered", "deliveryId": str(delivery.id), "duplicate": False}
