import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.outbox import OutboxMetadata, enqueue_outbox_event
from app.modules.integrations.models import IntegrationConnection
from app.modules.operator.models import MarketingPlan, OperatorCommandReceipt, OperatorRun
from app.modules.operator.policies.operator_schedule_policy import (
    current_operator_period,
    next_operator_schedule,
    operator_zone,
)
from app.modules.products.models import Product, ProductProfile, WebsiteSource, product_repositories
from app.modules.repos.models import repos
from app.modules.search_intelligence.models import SearchSource
from app.modules.workspaces.models import Workspace
from app.shared.exceptions import ConflictError, NotFoundError

REDUCED_CAPABILITY_ALLOWED = True


@dataclass(frozen=True, slots=True)
class ActivationState:
    workspace: Workspace
    product: Product | None
    github_installed: bool
    monitored_repository_count: int
    approved_profile: ProductProfile | None
    website_source: WebsiteSource | None
    search_source: SearchSource | None
    active_run: OperatorRun | None
    current_run: OperatorRun | None
    latest_report: MarketingPlan | None

    @property
    def website_ready(self) -> bool:
        return bool(
            self.website_source
            and str(self.website_source.status) == "active"
            and self.website_source.last_successful_crawl_at
        )

    @property
    def search_connected(self) -> bool:
        return bool(
            self.search_source
            and self.search_source.status == "active"
            and self.search_source.latest_successful_data_date
        )

    @property
    def search_ready(self) -> bool:
        if self.product is None:
            return False
        return self.search_connected or (
            self.product.search_mode == "deferred_reduced" and REDUCED_CAPABILITY_ALLOWED
        )

    @property
    def source_blockers(self) -> list[str]:
        blockers: list[str] = []
        if self.product is None:
            return ["activation_product_required"]
        if self.approved_profile is None:
            blockers.append("approved_product_profile_required")
        if not self.website_ready:
            blockers.append("successful_website_crawl_required")
        if not self.search_ready:
            blockers.append("search_connection_or_explicit_reduced_mode_required")
        return blockers

    @property
    def run_blockers(self) -> list[str]:
        blockers = list(self.source_blockers)
        if self.product is None:
            return ["activation_product_required", *blockers]
        if not self.product.weekly_growth_operator_enabled:
            blockers.append("operator_disabled")
        if not self.product.operator_manual_enabled:
            blockers.append("manual_runs_disabled")
        return blockers


async def read_activation_state(
    db: AsyncSession, workspace_id: UUID, *, lock: bool = False
) -> ActivationState:
    workspace_query = select(Workspace).where(Workspace.id == workspace_id)
    if lock:
        workspace_query = workspace_query.with_for_update()
    workspace = await db.scalar(workspace_query)
    if workspace is None:
        raise NotFoundError("Activation not found.", code="activation_not_found")
    product = None
    if workspace.activation_product_id is not None:
        product_query = select(Product).where(
            Product.workspace_id == workspace_id,
            Product.id == workspace.activation_product_id,
        )
        if lock:
            product_query = product_query.with_for_update()
        product = await db.scalar(product_query)
    if product is None:
        return ActivationState(workspace, None, False, 0, None, None, None, None, None, None)
    github_installed = bool(
        await db.scalar(
            select(func.count())
            .select_from(IntegrationConnection)
            .where(
                IntegrationConnection.workspace_id == workspace_id,
                IntegrationConnection.provider == "github_app",
                IntegrationConnection.status == "active",
                IntegrationConnection.deleted_at.is_(None),
            )
        )
    )
    monitored = int(
        await db.scalar(
            select(func.count())
            .select_from(
                product_repositories.join(
                    repos,
                    (repos.c.workspace_id == product_repositories.c.workspace_id)
                    & (repos.c.id == product_repositories.c.repo_id),
                )
            )
            .where(
                product_repositories.c.workspace_id == workspace_id,
                product_repositories.c.product_id == product.id,
                repos.c.is_tracked.is_(True),
            )
        )
        or 0
    )
    profile = await db.scalar(
        select(ProductProfile)
        .where(
            ProductProfile.workspace_id == workspace_id,
            ProductProfile.product_id == product.id,
            ProductProfile.status == "approved",
            ProductProfile.version.is_not(None),
            ProductProfile.deleted_at.is_(None),
        )
        .order_by(ProductProfile.version.desc())
        .limit(1)
    )
    website = await db.scalar(
        select(WebsiteSource).where(
            WebsiteSource.workspace_id == workspace_id, WebsiteSource.product_id == product.id
        )
    )
    search = await db.scalar(
        select(SearchSource)
        .where(
            SearchSource.workspace_id == workspace_id,
            SearchSource.product_id == product.id,
            SearchSource.status == "active",
        )
        .order_by(SearchSource.selected_at.desc())
        .limit(1)
    )
    current_run = await db.scalar(
        select(OperatorRun)
        .where(
            OperatorRun.workspace_id == workspace_id,
            OperatorRun.product_id == product.id,
            OperatorRun.run_kind == "weekly_growth",
        )
        .order_by(OperatorRun.started_at.desc(), OperatorRun.id.desc())
        .limit(1)
    )
    active_run = await db.scalar(
        select(OperatorRun)
        .where(
            OperatorRun.workspace_id == workspace_id,
            OperatorRun.product_id == product.id,
            OperatorRun.run_kind == "weekly_growth",
            OperatorRun.status.in_(("pending", "running")),
        )
        .order_by(OperatorRun.started_at.desc(), OperatorRun.id.desc())
        .limit(1)
    )
    report = await db.scalar(
        select(MarketingPlan)
        .where(
            MarketingPlan.workspace_id == workspace_id,
            MarketingPlan.product_id == product.id,
            MarketingPlan.status == "ready",
        )
        .order_by(MarketingPlan.finalized_at.desc(), MarketingPlan.plan_revision.desc())
        .limit(1)
    )
    return ActivationState(
        workspace,
        product,
        github_installed,
        monitored,
        profile,
        website,
        search,
        active_run,
        current_run,
        report,
    )


async def require_operator_run_ready(
    db: AsyncSession, workspace_id: UUID, product_id: UUID
) -> ActivationState:
    requested = await db.scalar(
        select(Product).where(Product.workspace_id == workspace_id, Product.id == product_id)
    )
    if requested is None:
        raise NotFoundError("Product not found.", code="product_not_found")
    if not requested.weekly_growth_operator_enabled or not requested.operator_manual_enabled:
        raise ConflictError("Manual operator runs are disabled.", code="manual_runs_disabled")
    state = await read_activation_state(db, workspace_id)
    if state.product is None or state.product.id != product_id:
        raise ConflictError(
            "Select this product for activation before running the operator.",
            code="activation_product_mismatch",
        )
    if state.run_blockers:
        raise ConflictError(
            "Operator readiness requirements are not met.",
            code=state.run_blockers[0],
            metadata={"blockers": state.run_blockers},
        )
    return state


def command_fingerprint(payload: dict[str, object]) -> str:
    value = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(value).hexdigest()


async def command_replay(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    operation: str,
    idempotency_key: str,
    fingerprint: str,
) -> OperatorCommandReceipt | None:
    receipt = await db.scalar(
        select(OperatorCommandReceipt).where(
            OperatorCommandReceipt.workspace_id == workspace_id,
            OperatorCommandReceipt.product_id == product_id,
            OperatorCommandReceipt.operation == operation,
            OperatorCommandReceipt.resource_id == product_id,
            OperatorCommandReceipt.idempotency_key == idempotency_key,
        )
    )
    if receipt and receipt.payload_fingerprint != fingerprint:
        raise ConflictError(
            "Idempotency key was used with another payload.",
            code="idempotency_payload_conflict",
        )
    return receipt


def add_command_receipt(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    operation: str,
    idempotency_key: str,
    fingerprint: str,
    result_resource_id: UUID,
) -> None:
    db.add(
        OperatorCommandReceipt(
            workspace_id=workspace_id,
            product_id=product_id,
            operation=operation,
            resource_id=product_id,
            idempotency_key=idempotency_key,
            payload_fingerprint=fingerprint,
            result_resource_id=result_resource_id,
        )
    )


async def set_activation_product(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    idempotency_key: str,
) -> None:
    state = await read_activation_state(db, workspace_id, lock=True)
    product = await db.scalar(
        select(Product).where(Product.workspace_id == workspace_id, Product.id == product_id)
    )
    if product is None:
        raise NotFoundError("Product not found.", code="product_not_found")
    fingerprint = command_fingerprint({"productId": product_id})
    receipt = await db.scalar(
        select(OperatorCommandReceipt).where(
            OperatorCommandReceipt.workspace_id == workspace_id,
            OperatorCommandReceipt.operation == "activation.select_product",
            OperatorCommandReceipt.idempotency_key == idempotency_key,
        )
    )
    if receipt:
        if receipt.payload_fingerprint != fingerprint:
            raise ConflictError(
                "Idempotency key was used with another payload.",
                code="idempotency_payload_conflict",
            )
        return
    if state.workspace.onboarding_completed and state.workspace.activation_product_id != product_id:
        raise ConflictError(
            "Completed onboarding cannot be moved to another activation product.",
            code="activation_product_locked",
        )
    state.workspace.activation_product_id = product_id
    add_command_receipt(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        operation="activation.select_product",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        result_resource_id=product_id,
    )
    await db.commit()


async def set_search_mode(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    mode: str,
    idempotency_key: str,
) -> None:
    state = await read_activation_state(db, workspace_id, lock=True)
    if state.product is None or state.product.id != product_id:
        raise NotFoundError("Activation product not found.", code="activation_product_not_found")
    if mode == "deferred_reduced" and not REDUCED_CAPABILITY_ALLOWED:
        raise ConflictError(
            "Reduced capability is not allowed.", code="search_deferral_not_allowed"
        )
    if mode == "connected" and not state.search_connected:
        raise ConflictError(
            "A usable Search baseline is required for connected mode.",
            code="search_baseline_required",
        )
    fingerprint = command_fingerprint({"mode": mode})
    if await command_replay(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        operation="activation.search_mode",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    ):
        return
    state.product.search_mode = mode
    state.product.search_mode_decided_at = datetime.now(UTC)
    add_command_receipt(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        operation="activation.search_mode",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        result_resource_id=product_id,
    )
    await db.commit()


async def set_repository_evidence_mode(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    mode: str,
    idempotency_key: str,
) -> None:
    state = await read_activation_state(db, workspace_id, lock=True)
    if state.product is None or state.product.id != product_id:
        raise NotFoundError("Activation product not found.", code="activation_product_not_found")
    if mode == "connected" and not state.monitored_repository_count:
        raise ConflictError(
            "Explicitly monitor a product repository before selecting connected evidence.",
            code="monitored_product_repository_required",
        )
    fingerprint = command_fingerprint({"mode": mode})
    if await command_replay(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        operation="activation.repository_evidence_mode",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    ):
        return
    state.product.repository_evidence_mode = mode
    state.product.repository_evidence_mode_decided_at = datetime.now(UTC)
    add_command_receipt(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        operation="activation.repository_evidence_mode",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        result_resource_id=product_id,
    )
    await db.commit()


def projected_next_schedule(product: Product, now: datetime | None = None) -> datetime | None:
    if not product.operator_scheduled_enabled:
        return None
    return next_operator_schedule(
        now or datetime.now(UTC),
        timezone=product.operator_timezone,
        weekday=product.operator_weekday,
        hour=product.operator_hour,
    )


async def finalize_activation_and_start(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    actor_id: UUID,
    idempotency_key: str,
    values: dict[str, object],
) -> tuple[OperatorRun, bool]:
    from app.modules.operator.services.weekly_growth_service import create_weekly_run

    state = await read_activation_state(db, workspace_id, lock=True)
    product = state.product
    if product is None:
        raise ConflictError(
            "Select an activation product first.", code="activation_product_required"
        )
    if state.source_blockers:
        raise ConflictError(
            "Activation requirements are not met.",
            code=state.source_blockers[0],
            metadata={"blockers": state.source_blockers},
        )
    fingerprint = command_fingerprint(values)
    replay = await command_replay(
        db,
        workspace_id=workspace_id,
        product_id=product.id,
        operation="activation.finalize_and_start",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )
    if replay:
        run = await db.scalar(
            select(OperatorRun).where(
                OperatorRun.workspace_id == workspace_id,
                OperatorRun.product_id == product.id,
                OperatorRun.id == replay.result_resource_id,
            )
        )
        if run is None:
            raise ConflictError(
                "Activation replay run is unavailable.", code="activation_replay_invalid"
            )
        return run, True
    timezone = values.get("timezone")
    if isinstance(timezone, str):
        operator_zone(timezone)
        product.operator_timezone = timezone
    weekday = values.get("weekday")
    if isinstance(weekday, int):
        product.operator_weekday = weekday
    hour = values.get("hour")
    if isinstance(hour, int):
        product.operator_hour = hour
    scheduled = values.get("scheduled_enabled")
    if isinstance(scheduled, bool):
        product.operator_scheduled_enabled = scheduled
    email = values.get("email_enabled")
    if isinstance(email, bool):
        product.operator_email_enabled = email
    product.weekly_growth_operator_enabled = True
    product.operator_manual_enabled = True
    state.workspace.onboarding_completed = True
    await db.flush()
    period_start, period_end = current_operator_period(
        datetime.now(UTC), timezone=product.operator_timezone
    )
    run = state.active_run
    if run is None:
        run = await db.scalar(
            select(OperatorRun).where(
                OperatorRun.workspace_id == workspace_id,
                OperatorRun.product_id == product.id,
                OperatorRun.run_kind == "weekly_growth",
                OperatorRun.logical_period_start == period_start,
                OperatorRun.workflow_version == "weekly-growth-v1",
            )
        )
    if run is None:
        run = await create_weekly_run(
            db,
            workspace_id=workspace_id,
            product_id=product.id,
            actor_id=actor_id,
            period_start=period_start,
            period_end=period_end,
            workflow_version="weekly-growth-v1",
            trigger="manual",
            idempotency_key=f"activation:{idempotency_key}",
        )
        await enqueue_outbox_event(
            db,
            event_name="operator/weekly-growth.requested",
            schema_version=1,
            idempotency_key=str(run.id),
            aggregate_id=run.id,
            workspace_id=workspace_id,
            product_id=product.id,
            metadata=OutboxMetadata(
                {
                    "runId": str(run.id),
                    "workspaceId": str(workspace_id),
                    "productId": str(product.id),
                    "schemaVersion": 1,
                    "correlationId": str(run.id),
                }
            ),
        )
    add_command_receipt(
        db,
        workspace_id=workspace_id,
        product_id=product.id,
        operation="activation.finalize_and_start",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        result_resource_id=run.id,
    )
    await db.commit()
    return run, False
