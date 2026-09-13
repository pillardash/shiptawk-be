from dataclasses import dataclass
from datetime import UTC, datetime
from json import dumps
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.analytics.services.product_event_service import write_product_event
from app.modules.identity.models.users import User
from app.modules.operator.models import (
    MarketingPlan,
    PlanAction,
    PreparedAsset,
    WeeklyGrowthDelivery,
)
from app.modules.operator.services.canonical_plan_service import resolve_canonical_plan
from app.modules.operator.services.weekly_growth_service import snapshot_hash
from app.modules.products.models import Product, product_repositories
from app.modules.repos.models import repos
from app.services.email.base import (
    EmailAddress,
    EmailMessage,
    EmailSendOutcomeUnknown,
    EmailService,
)
from app.services.email.rendering import EmailSection, render_email


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
    asset_revisions = (
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
    assets_by_action: dict[UUID, PreparedAsset] = {}
    for asset in asset_revisions:
        if asset.action_id is not None and (
            asset.action_id not in assets_by_action
            or asset.revision > assets_by_action[asset.action_id].revision
        ):
            assets_by_action[asset.action_id] = asset
    assets = list(assets_by_action.values())
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
    shipped = snapshot.get("shippedSummary", {})
    search = snapshot.get("searchSummary", {})
    sections = [
        EmailSection("Search changes", dumps(search, indent=2, sort_keys=True)),
        EmailSection(
            "Best opportunity",
            actions[0].title if actions else "No evidence-backed opportunity this week.",
        ),
        EmailSection(
            f"Actions ({len(actions)})",
            "\n".join(
                f"{action.position}. {action.title}: {frontend_url}/history/actions/{action.id}"
                for action in actions
            )
            or "No actions.",
        ),
        EmailSection(
            "Prepared outputs",
            "\n".join(
                f"{asset.kind}: {frontend_url}/content/{asset.id}?revision={asset.revision}"
                for asset in sorted(assets, key=lambda item: (str(item.action_id), item.revision))
            )
            or "No prepared outputs.",
        ),
    ]
    if repository_rows:
        sections.insert(0, EmailSection("What shipped", dumps(shipped, indent=2, sort_keys=True)))
        sections.append(
            EmailSection(
                "Repository details",
                "\n".join(f"{name} ({role})" for name, role in repository_rows),
            )
        )
    else:
        sections.insert(
            0,
            EmailSection(
                "Evidence sources",
                "Website and Search Console evidence were used. Shipping evidence was not "
                "configured and was not evaluated.",
            ),
        )
    content = render_email(
        subject=f"{product.name}: weekly growth plan",
        preheader=f"Your weekly growth plan for {product.name} is ready.",
        heading=f"Weekly growth plan for {product.name}",
        body="Review the evidence-backed opportunities and prepared outputs for this week.",
        sections=sections,
        action_label="Review weekly plan",
        action_url=f"{frontend_url}/history/runs/{plan.operator_run_id}",
    )
    return WeeklyEmailProjection(
        subject=content.subject,
        text=content.text,
        html=content.html or "",
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
            canonical = await resolve_canonical_plan(
                db, workspace_id=workspace_id, product_id=product_id
            )
            if (
                canonical.plan is None
                or canonical.plan.id != plan_id
                or (canonical.plan.plan_revision or 1) != plan_revision
            ):
                return {
                    "status": "suppressed",
                    "reason": "superseded_plan_revision",
                    "planId": str(plan_id),
                }
            product = await db.scalar(
                select(Product).where(
                    Product.workspace_id == workspace_id, Product.id == product_id
                )
            )
            if product is None or not (
                product.weekly_growth_operator_enabled and product.operator_email_enabled
            ):
                return {"status": "suppressed", "planId": str(plan_id)}
            user = await db.scalar(select(User).where(User.id == product.user_id))
            if (
                user is None
                or not user.is_active
                or user.deleted_at is not None
                or not user.email_notifications_enabled
                or not user.email
            ):
                return {"status": "suppressed", "planId": str(plan_id)}
            recipient = user.email
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
            if delivery is not None and delivery.status in {"delivered", "unknown_outcome"}:
                return {
                    "status": delivery.status,
                    "deliveryId": str(delivery.id),
                    "duplicate": True,
                }
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
                receipt = self.email_service.send(
                    EmailMessage(
                        to=[EmailAddress(recipient)],
                        subject=projection.subject,
                        text=projection.text,
                        html=projection.html,
                    )
                )
            except EmailSendOutcomeUnknown:
                delivery.status = "unknown_outcome"
                await db.commit()
                return {
                    "status": "unknown_outcome",
                    "deliveryId": str(delivery.id),
                    "duplicate": False,
                }
            except Exception:
                delivery.status = "failed"
                await db.commit()
                raise
            delivery.status = "delivered"
            if receipt is not None:
                delivery.provider_delivery_id = receipt.message_id
                delivery.provider_receipt = {
                    "acceptedRecipients": list(receipt.accepted_recipients)
                }
            delivery.delivered_at = datetime.now(UTC)
            await write_product_event(
                db,
                workspace_id=workspace_id,
                product_id=product_id,
                actor_id=None,
                event_name="weekly_email_delivered",
                idempotency_key=f"weekly-email-delivered:{delivery.id}",
                resource_type="weekly_growth_delivery",
                resource_id=delivery.id,
                resource_revision=plan_revision,
            )
            await db.commit()
            return {"status": "delivered", "deliveryId": str(delivery.id), "duplicate": False}
