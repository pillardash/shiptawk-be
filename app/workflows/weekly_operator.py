from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.outbox import OutboxMetadata, enqueue_outbox_event
from app.modules.operator.models import MarketingPlan, OperatorRun, OperatorRunSnapshot, PlanAction
from app.modules.operator.policies.operator_schedule_policy import operator_zone
from app.modules.operator.services.operator_ai_service import OperatorAIService
from app.modules.operator.services.weekly_growth_service import (
    create_canonical_plan,
    create_weekly_run,
    detection_snapshot_summaries,
    finalize_plan,
    select_current_run_opportunities,
)
from app.modules.products.models import Product
from app.modules.products.services.activation_service import require_operator_run_ready
from app.shared.exceptions import ConflictError
from app.workflows.opportunity_detection import OpportunityDetectionWorkflow


class WeeklyOperatorTenantMismatchError(ValueError):
    pass


class WeeklyOperatorScheduler:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        now: Callable[[], datetime] | None = None,
        workflow_version: str = "weekly-growth-v1",
    ) -> None:
        self.sessions = sessions
        self.now = now or (lambda: datetime.now(UTC))
        self.workflow_version = workflow_version

    async def schedule(self) -> dict[str, int]:
        scheduled = skipped = 0
        current = self.now()
        async with self.sessions() as db:
            products = list(
                await db.scalars(
                    select(Product).where(
                        Product.weekly_growth_operator_enabled.is_(True),
                        Product.operator_scheduled_enabled.is_(True),
                    )
                )
            )
            for product in products:
                try:
                    zone = operator_zone(product.operator_timezone)
                    await require_operator_run_ready(db, product.workspace_id, product.id)
                except (ConflictError, ValueError):
                    skipped += 1
                    continue
                local = current.astimezone(zone)
                if (
                    local.weekday() != product.operator_weekday
                    or local.hour != product.operator_hour
                ):
                    skipped += 1
                    continue
                local_start = datetime.combine(local.date(), time.min, zone)
                period_start = local_start.astimezone(UTC)
                run = await create_weekly_run(
                    db,
                    workspace_id=product.workspace_id,
                    product_id=product.id,
                    actor_id=product.user_id,
                    period_start=period_start,
                    period_end=(local_start + timedelta(days=7)).astimezone(UTC),
                    workflow_version=self.workflow_version,
                    trigger="scheduled",
                )
                await enqueue_outbox_event(
                    db,
                    event_name="operator/weekly-growth.requested",
                    schema_version=1,
                    idempotency_key=str(run.id),
                    aggregate_id=run.id,
                    workspace_id=product.workspace_id,
                    product_id=product.id,
                    metadata=OutboxMetadata(
                        {
                            "runId": str(run.id),
                            "workspaceId": str(product.workspace_id),
                            "productId": str(product.id),
                            "schemaVersion": 1,
                            "correlationId": str(run.id),
                        }
                    ),
                )
                scheduled += 1
            await db.commit()
        return {"scheduled": scheduled, "skipped": skipped}


class WeeklyOperatorWorkflow:
    """Durable coordinator; Phase 4's workflow remains the sole detection mutation authority."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        detection: OpportunityDetectionWorkflow,
        ai: OperatorAIService,
    ) -> None:
        self.sessions = sessions
        self.detection = detection
        self.ai = ai

    async def process(
        self, run_id: UUID, workspace_id: UUID, product_id: UUID
    ) -> dict[str, object]:
        try:
            return await self._process(run_id, workspace_id, product_id)
        except WeeklyOperatorTenantMismatchError:
            raise
        except Exception as exc:
            async with self.sessions() as db:
                run = await db.scalar(
                    select(OperatorRun).where(
                        OperatorRun.id == run_id,
                        OperatorRun.workspace_id == workspace_id,
                        OperatorRun.product_id == product_id,
                        OperatorRun.run_kind == "weekly_growth",
                    )
                )
                if run is not None and run.status not in {"completed", "stopped"}:
                    run.status = "failed"
                    run.failure_category = type(exc).__name__[:64]
                    run.completed_at = datetime.now(UTC)
                    summary = dict(run.stage_summary or {})
                    summary["failure"] = {
                        "category": run.failure_category,
                        "stage": run.current_stage,
                    }
                    run.stage_summary = summary
                    await db.commit()
            raise

    async def _process(
        self, run_id: UUID, workspace_id: UUID, product_id: UUID
    ) -> dict[str, object]:
        async with self.sessions() as db:
            run = await db.scalar(
                select(OperatorRun).where(OperatorRun.id == run_id).with_for_update()
            )
            if run is None or run.run_kind != "weekly_growth":
                return {"status": "not_found", "runId": str(run_id)}
            if run.workspace_id != workspace_id or run.product_id != product_id:
                raise WeeklyOperatorTenantMismatchError(
                    "Weekly event tenant does not match its run."
                )
            existing_plan = await db.scalar(
                select(MarketingPlan).where(
                    MarketingPlan.workspace_id == workspace_id,
                    MarketingPlan.product_id == product_id,
                    MarketingPlan.operator_run_id == run_id,
                )
            )
            if existing_plan is not None and existing_plan.status == "ready":
                return {
                    "status": "completed",
                    "runId": str(run_id),
                    "planId": str(existing_plan.id),
                }
            run.status = "running"
            run.current_stage = "detecting"
            summary = dict(run.stage_summary or {})
            detection_run_id = (
                UUID(str(summary["detectionRunId"])) if summary.get("detectionRunId") else None
            )
            if detection_run_id is None:
                detection_run = OperatorRun(
                    workspace_id=workspace_id,
                    product_id=product_id,
                    run_kind="opportunity_detection",
                    trigger=run.trigger,
                    status="pending",
                    idempotency_key=f"{run.idempotency_key}:detection",
                    request_fingerprint="weekly-child-v1",
                    input_schema_version="1",
                    detector_suite_version="v1",
                    quality_policy_version="v1",
                    scoring_policy_version="v1",
                    cooldown_policy_version="v1",
                    analyzer_version="opportunity-detection-v1",
                    provenance={"weeklyRunId": str(run.id)},
                    summary={},
                    created_by_actor_id=run.created_by_actor_id,
                )
                db.add(detection_run)
                await db.flush()
                detection_run_id = detection_run.id
                summary["detectionRunId"] = str(detection_run_id)
                run.stage_summary = summary
            await db.commit()

        detection_result = await self.detection.process(detection_run_id, workspace_id, product_id)
        if detection_result["status"] != "completed":
            async with self.sessions() as db:
                run = await db.get(OperatorRun, run_id)
                assert run is not None
                run.status = "stopped"
                run.stopped_reason = str(detection_result.get("summary", {}))[:500]
                await db.commit()
            return {"status": "stopped", "runId": str(run_id), "detection": detection_result}

        async with self.sessions() as db:
            run = await db.get(OperatorRun, run_id)
            assert run is not None
            opportunities = await select_current_run_opportunities(
                db, run=run, detection_run_id=detection_run_id
            )
            await db.commit()

        recommendations = []
        no_recommendations: list[dict[str, object]] = []
        for opportunity in opportunities:
            recommendation = await self.ai.generate_recommendation(
                workspace_id=workspace_id,
                product_id=product_id,
                opportunity=opportunity,
                idempotency_key=f"{run_id}:recommendation:{opportunity.id}",
                lease_owner=str(run_id),
            )
            if recommendation.kind == "no_recommendation":
                no_recommendations.append(
                    {
                        "opportunityId": str(opportunity.id),
                        "reason": recommendation.output.get("reason"),
                        "evidenceIds": recommendation.output.get("evidence_ids", []),
                    }
                )
            else:
                recommendations.append(recommendation)

        async with self.sessions() as db:
            run = await db.get(OperatorRun, run_id)
            assert run is not None
            plan = await db.scalar(
                select(MarketingPlan).where(MarketingPlan.operator_run_id == run_id)
            )
            if plan is None:
                plan = await create_canonical_plan(
                    db,
                    run=run,
                    actor_id=run.created_by_actor_id,
                    recommendations=recommendations,
                    selection_policy_version="phase4-priority-v1",
                )
            await db.commit()
            actions = list(
                await db.scalars(
                    select(PlanAction)
                    .where(PlanAction.plan_id == plan.id)
                    .order_by(PlanAction.position)
                )
            )

        by_recommendation = {item.id: item for item in recommendations}
        for action in actions:
            if action.recommendation_id is None:
                raise RuntimeError("Canonical weekly action has no recommendation.")
            recommendation = by_recommendation[action.recommendation_id]
            await self.ai.process_accepted_recommendation(
                recommendation=recommendation,
                output_type=str(recommendation.output["output_type"]),
                action_id=action.id,
                idempotency_key=f"{run_id}:asset:{recommendation.id}:1",
                lease_owner=str(run_id),
            )

        async with self.sessions() as db:
            snapshot = await db.scalar(
                select(OperatorRunSnapshot).where(
                    OperatorRunSnapshot.workspace_id == workspace_id,
                    OperatorRunSnapshot.product_id == product_id,
                    OperatorRunSnapshot.operator_run_id == detection_run_id,
                )
            )
            if snapshot is None:
                raise RuntimeError("Completed detection run has no immutable input snapshot.")
            shipped_summary, search_summary = detection_snapshot_summaries(snapshot.input_payload)
            plan = await finalize_plan(
                db,
                workspace_id=workspace_id,
                product_id=product_id,
                plan_id=plan.id,
                no_recommendations=no_recommendations,
                shipped_summary=shipped_summary,
                search_summary=search_summary,
            )
            run = await db.get(OperatorRun, run_id)
            assert run is not None
            run.status = "completed"
            run.current_stage = "ready"
            run.finalized_at = datetime.now(UTC)
            run.summary = {"planId": str(plan.id), "actionCount": plan.action_count or 0}
            await enqueue_outbox_event(
                db,
                event_name="operator/weekly-growth.ready",
                schema_version=1,
                idempotency_key=f"{plan.id}:{plan.plan_revision or 1}",
                aggregate_id=plan.id,
                workspace_id=workspace_id,
                product_id=product_id,
                metadata=OutboxMetadata(
                    {
                        "runId": str(run.id),
                        "planId": str(plan.id),
                        "planRevision": plan.plan_revision or 1,
                        "workspaceId": str(workspace_id),
                        "productId": str(product_id),
                        "schemaVersion": 1,
                        "correlationId": str(run.id),
                    }
                ),
            )
            await db.commit()
        return {"status": "completed", "runId": str(run_id), "planId": str(plan.id)}
