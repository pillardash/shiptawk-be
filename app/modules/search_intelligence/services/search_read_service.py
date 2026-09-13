from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.search_intelligence.enums.search_intelligence_enum import (
    SearchBaselineItemKind,
    SearchBaselineView,
    SearchCapabilityReduction,
)
from app.modules.search_intelligence.models import SearchDailyMetric
from app.modules.search_intelligence.policies.search_baseline_policy import (
    BASELINE_POLICY_VERSION,
    DatePeriod,
    aggregate_search_metrics,
    baseline_status,
    calculate_complete_periods,
    classify_query_baseline,
    is_high_impression_low_ctr_page,
)
from app.modules.search_intelligence.policies.search_health_policy import search_health_state
from app.modules.search_intelligence.providers.search.search_provider import (
    SearchProviderCapabilities,
)
from app.modules.search_intelligence.repositories.search_read_repository import (
    latest_provider_connection,
    latest_source,
    metric_date_bounds,
    metrics_for_range,
    source_context,
    source_pages,
    source_queries,
    source_runs,
)
from app.modules.search_intelligence.schemas.search_read_schema import (
    SearchBaselineResponse,
    SearchBaselineViews,
    SearchComparisonItem,
    SearchHealthResponse,
    SearchMetrics,
    SearchPageItem,
    SearchPagePageResponse,
    SearchPeriod,
    SearchQueryItem,
    SearchQueryPageResponse,
    SearchReadinessResponse,
)
from app.modules.search_intelligence.services.search_connection_service import (
    GOOGLE_PROVIDER,
    require_product_access,
)
from app.shared.exceptions import BadRequestError

MAX_READ_DAYS = 90
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
BASELINE_VIEW_LIMIT = 10


def _metrics(rows: list[SearchDailyMetric]) -> SearchMetrics:
    value = aggregate_search_metrics(
        (row.clicks, row.impressions, row.average_position) for row in rows
    )
    return SearchMetrics(
        clicks=value.clicks,
        impressions=value.impressions,
        ctr=value.ctr,
        average_position=value.average_position,
    )


def _period(period: DatePeriod) -> SearchPeriod:
    return SearchPeriod(start_date=period.start, end_date=period.end)


def _comparison(
    identity: UUID,
    label: str,
    current_rows: list[SearchDailyMetric],
    prior_rows: list[SearchDailyMetric],
    *,
    item_kind: SearchBaselineItemKind,
) -> SearchComparisonItem:
    current = _metrics(current_rows)
    prior = _metrics(prior_rows)
    return SearchComparisonItem(
        id=identity,
        label=label,
        current=current,
        prior=prior,
        click_change=current.clicks - prior.clicks,
        impression_change=current.impressions - prior.impressions,
        item_kind=item_kind,
    )


def _validate_pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > MAX_LIMIT or offset < 0:
        raise BadRequestError("Search pagination is invalid.", code="search_pagination_invalid")


async def _data_quality(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    source_id: UUID,
    period: DatePeriod,
) -> tuple[bool, list[str]]:
    runs = await source_runs(db, workspace_id, product_id, source_id)
    truncated = any(
        run.status == "succeeded"
        and run.is_truncated
        and run.requested_start_date <= period.end
        and run.requested_end_date >= period.start
        for run in runs
    )
    return truncated, ["query_page_data_truncated"] if truncated else []


def _read_period(
    *, start_date: date | None, end_date: date | None, expected_complete_date: date
) -> DatePeriod:
    end = end_date or expected_complete_date
    start = start_date or end - timedelta(days=27)
    if start > end:
        raise BadRequestError("Search dates are invalid.", code="search_dates_invalid")
    if end > expected_complete_date:
        raise BadRequestError("Search dates are not complete.", code="search_dates_incomplete")
    if (end - start).days + 1 > MAX_READ_DAYS:
        raise BadRequestError("Search date range is too large.", code="search_dates_too_large")
    return DatePeriod(start, end)


def expected_complete_date(capabilities: SearchProviderCapabilities, now: datetime) -> date:
    return now.astimezone(ZoneInfo(capabilities.reporting_timezone)).date() - timedelta(
        days=capabilities.finalization_lag_days
    )


def _public_failure_category(category: str | None) -> str | None:
    if category is None:
        return None
    lowered = category.lower()
    if any(value in lowered for value in ("auth", "credential", "token")):
        return "authorization"
    if "rate" in lowered or "quota" in lowered:
        return "rate_limit"
    if "invalid" in lowered:
        return "invalid_provider_output"
    if any(value in lowered for value in ("timeout", "transport", "network")):
        return "transport"
    return "provider_error"


async def read_baseline(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    capabilities: SearchProviderCapabilities,
    cutoff_date: date | None,
    now: datetime | None = None,
) -> SearchBaselineResponse:
    await require_product_access(db, workspace_id, product_id, actor_id, write=False)
    source = await latest_source(db, workspace_id, product_id)
    expected = expected_complete_date(capabilities, now or datetime.now(UTC))
    cutoff = cutoff_date or expected
    if cutoff > expected or cutoff < expected - timedelta(days=MAX_READ_DAYS - 1):
        raise BadRequestError("Baseline cutoff is invalid.", code="search_cutoff_invalid")
    periods = calculate_complete_periods(cutoff)
    empty = SearchMetrics(clicks=0, impressions=0, ctr=None, average_position=None)
    empty_views = SearchBaselineViews(
        top_growing_queries=[],
        top_declining_queries=[],
        top_growing_pages=[],
        top_declining_pages=[],
        queries_within_striking_distance=[],
        high_impression_low_ctr=[],
        high_impression_low_ctr_pages=[],
        newly_appearing_queries=[],
        queries_without_relevant_page=[],
    )
    expected_dates = [periods.prior.start + timedelta(days=i) for i in range(56)]
    if source is None or source.status != "active":
        return SearchBaselineResponse(
            status="pending",
            policy_version=BASELINE_POLICY_VERSION,
            cutoff_date=cutoff,
            current_period=_period(periods.current),
            prior_period=_period(periods.prior),
            current=empty,
            prior=empty,
            missing_dates=expected_dates,
            warnings=[],
            views=empty_views,
        )
    if cutoff_date is not None:
        first_stored, last_stored = await metric_date_bounds(
            db, workspace_id, product_id, source.id
        )
        if (
            first_stored is None
            or last_stored is None
            or periods.prior.start < first_stored
            or cutoff > last_stored
        ):
            raise BadRequestError(
                "Baseline cutoff is outside stored data.", code="search_cutoff_not_stored"
            )
    rows = await metrics_for_range(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        source_id=source.id,
        start_date=periods.prior.start,
        end_date=periods.current.end,
    )
    site = [row for row in rows if row.grain == "site_total"]
    observed = {row.metric_date for row in site}
    _, current_missing = baseline_status(periods.current, observed)
    _, prior_missing = baseline_status(periods.prior, observed)
    missing = tuple(sorted((*prior_missing, *current_missing)))
    status = "pending" if not observed else "partial" if missing else "ready"
    current_totals = _metrics(
        [row for row in site if periods.current.start <= row.metric_date <= periods.current.end]
    )
    prior_totals = _metrics(
        [row for row in site if periods.prior.start <= row.metric_date <= periods.prior.end]
    )
    if status == "ready" and current_totals.impressions == 0 and prior_totals.impressions == 0:
        status = "no_data"

    queries = await source_queries(db, workspace_id, product_id, source.id)
    pages = await source_pages(db, workspace_id, product_id, source.id)
    facts = [row for row in rows if row.grain == "query_page"]
    by_query: dict[UUID, list[SearchDailyMetric]] = defaultdict(list)
    by_page: dict[UUID, list[SearchDailyMetric]] = defaultdict(list)
    for row in facts:
        if row.query_id is not None:
            by_query[row.query_id].append(row)
        if row.page_id is not None:
            by_page[row.page_id].append(row)

    def split(
        items: list[SearchDailyMetric],
    ) -> tuple[list[SearchDailyMetric], list[SearchDailyMetric]]:
        return (
            [r for r in items if periods.current.start <= r.metric_date <= periods.current.end],
            [r for r in items if periods.prior.start <= r.metric_date <= periods.prior.end],
        )

    query_items: list[tuple[SearchComparisonItem, tuple[SearchBaselineView, ...]]] = []
    for query_id, query_rows in by_query.items():
        query = queries.get(query_id)
        if query is None:
            continue
        current_rows, prior_rows = split(query_rows)
        item = _comparison(
            query_id,
            query.query_text,
            current_rows,
            prior_rows,
            item_kind=SearchBaselineItemKind.query,
        )
        matched = any(
            row.page_id in pages and pages[row.page_id].match_status == "matched"
            for row in current_rows
            if row.page_id is not None
        )
        classified = classify_query_baseline(
            current_clicks=item.current.clicks,
            previous_clicks=item.prior.clicks,
            current_impressions=item.current.impressions,
            previous_impressions=item.prior.impressions,
            current_position=item.current.average_position,
            has_confident_page=matched,
        )
        query_items.append((item, classified.views))
    page_items = []
    for page_id, page_rows in by_page.items():
        page = pages.get(page_id)
        if page is None:
            continue
        current_rows, prior_rows = split(page_rows)
        page_items.append(
            _comparison(
                page_id,
                page.original_url,
                current_rows,
                prior_rows,
                item_kind=SearchBaselineItemKind.page,
            )
        )

    def query_view(view: SearchBaselineView, *, reverse: bool = True) -> list[SearchComparisonItem]:
        selected = [item for item, views in query_items if view in views]
        return sorted(
            selected,
            key=lambda item: (
                -item.click_change if reverse else item.click_change,
                item.label.casefold(),
                str(item.id),
            ),
        )[:BASELINE_VIEW_LIMIT]

    growing_pages = [item for item in page_items if item.click_change > 0]
    declining_pages = [item for item in page_items if item.click_change < 0]
    growing_pages.sort(key=lambda item: (-item.click_change, item.label.casefold(), str(item.id)))
    declining_pages.sort(key=lambda item: (item.click_change, item.label.casefold(), str(item.id)))
    low_ctr_pages = [
        item
        for item in page_items
        if is_high_impression_low_ctr_page(
            current_clicks=item.current.clicks,
            current_impressions=item.current.impressions,
            current_position=item.current.average_position,
        )
    ]
    low_ctr_pages.sort(
        key=lambda item: (-item.current.impressions, item.label.casefold(), str(item.id))
    )
    warnings: list[str] = []
    runs = await source_runs(db, workspace_id, product_id, source.id)
    if any(
        run.is_truncated
        and run.requested_start_date <= periods.current.end
        and run.requested_end_date >= periods.prior.start
        for run in runs
    ):
        warnings.append("query_page_data_truncated")
    if missing:
        warnings.append("site_total_dates_missing")
    return SearchBaselineResponse(
        status=status,
        policy_version=BASELINE_POLICY_VERSION,
        cutoff_date=cutoff,
        current_period=_period(periods.current),
        prior_period=_period(periods.prior),
        current=current_totals,
        prior=prior_totals,
        missing_dates=list(missing),
        warnings=warnings,
        views=SearchBaselineViews(
            top_growing_queries=query_view(SearchBaselineView.top_growing_queries),
            top_declining_queries=query_view(
                SearchBaselineView.top_declining_queries, reverse=False
            ),
            top_growing_pages=growing_pages[:BASELINE_VIEW_LIMIT],
            top_declining_pages=declining_pages[:BASELINE_VIEW_LIMIT],
            queries_within_striking_distance=query_view(
                SearchBaselineView.queries_within_striking_distance
            ),
            high_impression_low_ctr=query_view(SearchBaselineView.high_impression_low_ctr),
            high_impression_low_ctr_pages=low_ctr_pages[:BASELINE_VIEW_LIMIT],
            newly_appearing_queries=query_view(SearchBaselineView.newly_appearing_queries),
            queries_without_relevant_page=query_view(
                SearchBaselineView.queries_without_confident_page
            ),
        ),
    )


async def read_queries(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    capabilities: SearchProviderCapabilities,
    start_date: date | None,
    end_date: date | None,
    limit: int,
    offset: int,
    now: datetime | None = None,
) -> SearchQueryPageResponse:
    await require_product_access(db, workspace_id, product_id, actor_id, write=False)
    _validate_pagination(limit, offset)
    period = _read_period(
        start_date=start_date,
        end_date=end_date,
        expected_complete_date=expected_complete_date(capabilities, now or datetime.now(UTC)),
    )
    source = await latest_source(db, workspace_id, product_id)
    if source is None or source.status != "active":
        return SearchQueryPageResponse(
            start_date=period.start,
            end_date=period.end,
            total=0,
            limit=limit,
            offset=offset,
            is_truncated=False,
            data_quality_warnings=[],
            items=[],
        )
    rows = await metrics_for_range(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        source_id=source.id,
        start_date=period.start,
        end_date=period.end,
    )
    queries = await source_queries(db, workspace_id, product_id, source.id)
    pages = await source_pages(db, workspace_id, product_id, source.id)
    grouped: dict[UUID, list[SearchDailyMetric]] = defaultdict(list)
    for row in rows:
        if row.grain == "query_page" and row.query_id is not None:
            grouped[row.query_id].append(row)
    items = []
    for query_id, query_rows in grouped.items():
        query = queries.get(query_id)
        if query is None:
            continue
        related = {row.page_id for row in query_rows if row.page_id in pages}
        matched = {page_id for page_id in related if pages[page_id].match_status == "matched"}
        ambiguous = any(pages[page_id].match_status == "ambiguous" for page_id in related)
        items.append(
            SearchQueryItem(
                id=query_id,
                text=query.query_text,
                metrics=_metrics(query_rows),
                matched_page_status=(
                    "matched" if matched else "ambiguous" if ambiguous else "unmatched"
                ),
                matched_page_count=len(matched),
            )
        )
    items.sort(key=lambda item: (-item.metrics.impressions, item.text.casefold(), str(item.id)))
    is_truncated, warnings = await _data_quality(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        source_id=source.id,
        period=period,
    )
    return SearchQueryPageResponse(
        start_date=period.start,
        end_date=period.end,
        total=len(items),
        limit=limit,
        offset=offset,
        is_truncated=is_truncated,
        data_quality_warnings=warnings,
        items=items[offset : offset + limit],
    )


async def read_pages(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    capabilities: SearchProviderCapabilities,
    start_date: date | None,
    end_date: date | None,
    limit: int,
    offset: int,
    now: datetime | None = None,
) -> SearchPagePageResponse:
    await require_product_access(db, workspace_id, product_id, actor_id, write=False)
    _validate_pagination(limit, offset)
    period = _read_period(
        start_date=start_date,
        end_date=end_date,
        expected_complete_date=expected_complete_date(capabilities, now or datetime.now(UTC)),
    )
    source = await latest_source(db, workspace_id, product_id)
    if source is None or source.status != "active":
        return SearchPagePageResponse(
            start_date=period.start,
            end_date=period.end,
            total=0,
            limit=limit,
            offset=offset,
            is_truncated=False,
            data_quality_warnings=[],
            items=[],
        )
    rows = await metrics_for_range(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        source_id=source.id,
        start_date=period.start,
        end_date=period.end,
    )
    pages = await source_pages(db, workspace_id, product_id, source.id)
    grouped: dict[UUID, list[SearchDailyMetric]] = defaultdict(list)
    for row in rows:
        if row.grain == "query_page" and row.page_id is not None:
            grouped[row.page_id].append(row)
    items = [
        SearchPageItem(
            id=page_id,
            provider_url=pages[page_id].original_url,
            website_page_id=pages[page_id].website_page_id,
            match_status=pages[page_id].match_status,
            match_method=pages[page_id].match_method,
            metrics=_metrics(page_rows),
        )
        for page_id, page_rows in grouped.items()
        if page_id in pages
    ]
    items.sort(key=lambda item: (-item.metrics.impressions, item.provider_url, str(item.id)))
    is_truncated, warnings = await _data_quality(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        source_id=source.id,
        period=period,
    )
    return SearchPagePageResponse(
        start_date=period.start,
        end_date=period.end,
        total=len(items),
        limit=limit,
        offset=offset,
        is_truncated=is_truncated,
        data_quality_warnings=warnings,
        items=items[offset : offset + limit],
    )


async def read_health(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    capabilities: SearchProviderCapabilities,
    now: datetime | None = None,
) -> SearchHealthResponse:
    await require_product_access(db, workspace_id, product_id, actor_id, write=False)
    source = await latest_source(db, workspace_id, product_id)
    connection = (
        (await source_context(db, source))[0]
        if source is not None
        else await latest_provider_connection(db, workspace_id, GOOGLE_PROVIDER)
    )
    property_record = (await source_context(db, source))[1] if source is not None else None
    runs = await source_runs(db, workspace_id, product_id, source.id) if source else []
    last = runs[0] if runs else None
    successful = next((run for run in runs if run.status == "succeeded"), None)
    active = next((run for run in runs if run.status in {"pending", "running"}), None)
    expected = expected_complete_date(capabilities, now or datetime.now(UTC))
    data_through = source.latest_successful_data_date if source else None
    successful_sync = successful is not None
    state = search_health_state(
        connection_status=connection.status if connection else None,
        source_status=source.status if source and source.status == "active" else None,
        active_run=active is not None,
        last_run_status=last.status if last else None,
        successful_sync=successful_sync,
        successful_zero_rows=successful is not None and successful.rows_received == 0,
        data_through_date=data_through,
        expected_complete_date=expected,
    )
    warnings: list[str] = []
    if successful and successful.is_truncated:
        warnings.append("query_page_data_truncated")
    if data_through is not None:
        assert source is not None
        site_rows = await metrics_for_range(
            db,
            workspace_id=workspace_id,
            product_id=product_id,
            source_id=source.id,
            start_date=max(data_through - timedelta(days=27), source.selected_at.date()),
            end_date=data_through,
        )
        observed = {row.metric_date for row in site_rows if row.grain == "site_total"}
        if observed:
            first = min(observed)
            span = range((data_through - first).days + 1)
            if any(first + timedelta(days=i) not in observed for i in span):
                warnings.append("site_total_dates_missing")
    public_provider = None
    if connection is not None:
        public_provider = (
            "google" if connection.provider == GOOGLE_PROVIDER else connection.provider
        )
    return SearchHealthResponse(
        state=state,
        provider=public_provider,
        connection_status=connection.status if connection else None,
        source_status=source.status if source else None,
        property_id=source.property_id if source else None,
        provider_property_id=property_record.provider_property_id if property_record else None,
        last_attempted_sync_at=last.created_at if last else None,
        last_successful_sync_at=successful.completed_at if successful else None,
        data_through_date=data_through,
        expected_complete_date=expected,
        freshness_lag_days=max(0, (expected - data_through).days) if data_through else None,
        active_run_status=active.status if active else None,
        active_sync_run_id=active.id if active else None,
        failure_category=(
            _public_failure_category(last.failure_category)
            if last and last.status == "failed"
            else None
        ),
        warnings=warnings,
        can_reconnect=connection is None or connection.status in {"expired", "revoked", "error"},
        can_change_property=connection is not None and connection.status == "active",
        can_resync=source is not None and source.status == "active" and active is None,
        can_disconnect_product=source is not None and source.status == "active",
        can_revoke_workspace_connection=(connection is not None and connection.status != "revoked"),
    )


async def read_readiness(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    actor_id: UUID,
    capabilities: SearchProviderCapabilities,
    now: datetime | None = None,
) -> SearchReadinessResponse:
    health = await read_health(
        db,
        workspace_id=workspace_id,
        product_id=product_id,
        actor_id=actor_id,
        capabilities=capabilities,
        now=now,
    )
    setup_complete = health.state in {"syncing", "healthy", "stale", "no_data", "error"}
    baseline_usable = health.state in {"healthy", "stale"}
    reductions = (
        []
        if baseline_usable
        else [SearchCapabilityReduction.search_dependent_recommendations_unavailable]
    )
    return SearchReadinessResponse(
        setup_complete=setup_complete,
        baseline_usable=baseline_usable,
        blocking_reasons=[],
        capability_reductions=reductions,
        health_status=health.state,
        active_sync_run_id=health.active_sync_run_id,
    )


__all__ = ["read_baseline", "read_health", "read_pages", "read_queries", "read_readiness"]
