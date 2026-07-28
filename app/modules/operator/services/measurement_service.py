from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.operator.models import MeasurementWindow
from app.modules.search_intelligence.models import SearchDailyMetric

SEARCH_METRICS = {"clicks", "impressions", "ctr", "position", "average_position"}


async def read_search_measurement(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    contract: dict[str, object],
    starts_on: date,
    ends_on: date,
) -> dict[str, object] | None:
    metric = str(contract.get("metric", ""))
    query_id = contract.get("queryId") or contract.get("query_id")
    page_id = contract.get("pageId") or contract.get("page_id")
    if metric not in SEARCH_METRICS or not query_id or not page_id:
        return None
    rows = list(
        await db.scalars(
            select(SearchDailyMetric).where(
                SearchDailyMetric.workspace_id == workspace_id,
                SearchDailyMetric.product_id == product_id,
                SearchDailyMetric.query_id == UUID(str(query_id)),
                SearchDailyMetric.page_id == UUID(str(page_id)),
                SearchDailyMetric.metric_date >= starts_on,
                SearchDailyMetric.metric_date < ends_on,
            )
        )
    )
    if not rows:
        return None
    clicks = sum(row.clicks for row in rows)
    impressions = sum(row.impressions for row in rows)
    positioned = [(row.average_position, row.impressions) for row in rows if row.average_position]
    position = (
        sum((value * weight for value, weight in positioned), Decimal(0))
        / sum(weight for _, weight in positioned)
        if positioned and sum(weight for _, weight in positioned)
        else None
    )
    values: dict[str, object] = {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": str(Decimal(clicks) / Decimal(impressions)) if impressions else None,
        "position": str(position) if position is not None else None,
        "queryId": str(query_id),
        "pageId": str(page_id),
    }
    return values


class MeasurementFollowupWorkflow:
    def __init__(self, *, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def process(
        self, measurement_id: UUID, workspace_id: UUID, product_id: UUID
    ) -> dict[str, object]:
        async with self.sessions() as db:
            measurement = await db.scalar(
                select(MeasurementWindow)
                .where(
                    MeasurementWindow.id == measurement_id,
                    MeasurementWindow.workspace_id == workspace_id,
                    MeasurementWindow.product_id == product_id,
                )
                .with_for_update()
            )
            if measurement is None:
                return {"status": "not_found"}
            if measurement.status in {"completed", "unavailable"}:
                return {"status": measurement.status, "duplicate": True}
            metric = str(measurement.metric_contract.get("metric", ""))
            if metric not in SEARCH_METRICS:
                measurement.status = "unavailable"
                measurement.outcome = "unavailable"
                measurement.unavailable_reason = "Metric requires manual measurement."
                measurement.limitations = ["No causal claim is available for non-search metrics."]
            else:
                current = await read_search_measurement(
                    db,
                    workspace_id=workspace_id,
                    product_id=product_id,
                    contract=measurement.metric_contract,
                    starts_on=measurement.starts_at.date(),
                    ends_on=measurement.ends_at.date(),
                )
                baseline = measurement.baseline_value
                if current is None or not baseline:
                    measurement.status = "unavailable"
                    measurement.outcome = "unavailable"
                    measurement.unavailable_reason = (
                        "Comparable authoritative search facts are unavailable."
                    )
                else:
                    key = "position" if metric == "average_position" else metric
                    before, after = baseline.get(key), current.get(key)
                    if before is None or after is None:
                        outcome = "inconclusive"
                    else:
                        before_decimal, after_decimal = Decimal(str(before)), Decimal(str(after))
                        if after_decimal == before_decimal:
                            outcome = "unchanged"
                        elif key == "position":
                            outcome = "improved" if after_decimal < before_decimal else "declined"
                        else:
                            outcome = "improved" if after_decimal > before_decimal else "declined"
                    measurement.status = "completed"
                    measurement.outcome = outcome
                    measurement.comparison_value = current
                    measurement.result = {"outcome": outcome}
                    measurement.limitations = ["Observed association only; no causal claim."]
            measurement.collected_at = datetime.now(UTC)
            await db.commit()
            return {
                "status": measurement.status,
                "outcome": measurement.outcome,
                "duplicate": False,
            }
