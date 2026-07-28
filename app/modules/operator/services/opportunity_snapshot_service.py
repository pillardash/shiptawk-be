import hashlib
import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.operator.models import OperatorRunSnapshot
from app.modules.operator.policies.lexical_match_policy import match_capability, query_profile_match
from app.modules.operator.repositories import opportunity_input_repository as repository
from app.modules.operator.schemas import (
    CapabilityMappingSnapshot,
    DetectionInput,
    MetricSnapshot,
    OpportunityHistoryItem,
    OpportunityHistorySnapshot,
    ProductProfileSnapshot,
    ProfileCapabilitySnapshot,
    QueryPerformanceSnapshot,
    SearchPagePerformanceSnapshot,
    SearchSnapshot,
    ShippingEventSnapshot,
    ShippingSnapshot,
    WebsitePageSnapshot,
    WebsiteSnapshot,
)
from app.modules.search_intelligence.models import SearchDailyMetric


class MissingProductError(LookupError):
    pass


class MissingApprovedProfileError(LookupError):
    pass


class SnapshotHashMismatchError(ValueError):
    pass


class InvalidShippingEvaluationOutcomeError(ValueError):
    pass


class DetectionInputAssembly(BaseModel):
    model_config = ConfigDict(frozen=True)

    detection_input: DetectionInput
    payload: dict[str, object]
    canonical_bytes: bytes
    input_hash: str


class SnapshotSession(Protocol):
    def add(self, instance: object) -> None: ...

    async def flush(self) -> None: ...


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _metric(rows: list[SearchDailyMetric]) -> MetricSnapshot:
    clicks = sum(row.clicks for row in rows)
    impressions = sum(row.impressions for row in rows)
    weighted_position = sum((row.average_position or Decimal(0)) * row.impressions for row in rows)
    return MetricSnapshot(
        clicks=clicks,
        impressions=impressions,
        ctr=Decimal(clicks) / impressions if impressions else Decimal(0),
        average_position=weighted_position / impressions if impressions else Decimal(0),
    )


def _split_metric(
    rows: list[SearchDailyMetric], current_start: date
) -> tuple[MetricSnapshot, MetricSnapshot]:
    return _metric([row for row in rows if row.metric_date >= current_start]), _metric(
        [row for row in rows if row.metric_date < current_start]
    )


def _heading_values(headings: list[dict[str, object]], level: int | None = None) -> tuple[str, ...]:
    values: list[str] = []
    for heading in headings:
        raw_level = heading.get("level")
        if level is not None and raw_level not in (level, f"h{level}", f"H{level}"):
            continue
        value = heading.get("text") or heading.get("value")
        if isinstance(value, str):
            values.append(value)
    return tuple(values)


def _shipping_outcome(value: object) -> str:
    raw = value.value if hasattr(value, "value") else value
    outcomes = {"generate": "generate", "ignore": "ignore", "hold": "review", "block": "review"}
    if not isinstance(raw, str) or raw not in outcomes:
        raise InvalidShippingEvaluationOutcomeError(
            f"Unsupported persisted shipping evaluation outcome: {raw!r}."
        )
    return outcomes[raw]


def _profile_capability(value: object) -> ProfileCapabilitySnapshot:
    if isinstance(value, str):
        return ProfileCapabilitySnapshot(capability_key=value, name=value)
    if isinstance(value, dict):
        key = value.get("capability_key") or value.get("key")
        name = value.get("name") or key
        if isinstance(key, str) and isinstance(name, str):
            description = value.get("description")
            active = value.get("active", True)
            return ProfileCapabilitySnapshot(
                capability_key=key,
                name=name,
                description=description if isinstance(description, str) else None,
                active=active if isinstance(active, bool) else True,
            )
    raise MissingApprovedProfileError("Approved profile contains an invalid important capability.")


async def _assemble_search(
    db: AsyncSession,
    workspace_id: UUID,
    product_id: UUID,
    as_of: datetime,
    profile: ProductProfileSnapshot,
) -> SearchSnapshot | None:
    # as_of records assembly time. Provider dates define windows, not a historical cutoff.
    authority = await repository.get_search_authority(db, workspace_id, product_id, as_of)
    if authority is None:
        return None
    source, run = authority
    current_end = min(source.latest_successful_data_date or run.requested_end_date, as_of.date())
    current_start = current_end - timedelta(days=27)
    prior_end = current_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=27)
    # Re-select because reporting lag can make the persisted cutoff earlier than as_of.
    authority = await repository.get_search_authority(
        db, workspace_id, product_id, as_of, prior_start, current_end
    )
    if authority is None:
        return None
    source, run = authority
    (
        metrics,
        queries,
        pages,
        website_pages,
        overlapping_truncation,
    ) = await repository.list_search_facts(
        db, workspace_id, product_id, source.id, prior_start, current_end, as_of
    )
    site_rows = [row for row in metrics if row.grain == "site_total"]
    detail_rows = [row for row in metrics if row.grain == "query_page"]
    expected_dates = {prior_start + timedelta(days=offset) for offset in range(56)}
    actual_dates = {row.metric_date for row in site_rows}
    missing_dates = tuple(sorted(expected_dates - actual_dates))
    query_by_id = {query.id: query for query in queries}
    page_by_id = {page.id: page for page in pages}
    website_fingerprint_by_id = {page.id: page.url_fingerprint for page in website_pages}
    by_query: dict[UUID, list[SearchDailyMetric]] = defaultdict(list)
    by_page: dict[UUID, list[SearchDailyMetric]] = defaultdict(list)
    query_pages: dict[UUID, set[UUID]] = defaultdict(set)
    for row in detail_rows:
        if row.query_id is not None and row.page_id is not None:
            by_query[row.query_id].append(row)
            by_page[row.page_id].append(row)
            query_pages[row.query_id].add(row.page_id)
    query_snapshots = []
    for query_id, rows in by_query.items():
        query = query_by_id.get(query_id)
        if query is None:
            continue
        associated = sorted(
            (page_by_id[page_id] for page_id in query_pages[query_id] if page_id in page_by_id),
            key=lambda item: item.url_fingerprint,
        )
        matched = [page for page in associated if page.match_status == "matched"]
        current, prior = _split_metric(rows, current_start)
        profile_match = query_profile_match(query.query_text, profile)
        query_snapshots.append(
            QueryPerformanceSnapshot(
                query_fingerprint=query.query_fingerprint,
                query_text=query.query_text,
                current=current,
                prior=prior,
                page_fingerprints=tuple(page.url_fingerprint for page in associated),
                matched_page_fingerprint=(
                    matched[0].url_fingerprint if len(matched) == 1 else None
                ),
                page_match_status=(
                    "matched"
                    if len(matched) == 1
                    else "ambiguous"
                    if len(matched) > 1
                    else "unmatched"
                ),
                profile_relevance=profile_match.relevance,
                branded=profile_match.branded,
                navigational=profile_match.navigational,
            )
        )
    page_snapshots = []
    for page_id, rows in by_page.items():
        page = page_by_id.get(page_id)
        if page is None:
            continue
        current, prior = _split_metric(rows, current_start)
        website_fingerprint = (
            website_fingerprint_by_id.get(page.website_page_id)
            if page.website_page_id is not None
            else None
        )
        page_snapshots.append(
            SearchPagePerformanceSnapshot(
                url_fingerprint=page.url_fingerprint,
                url=page.normalized_url,
                website_page_fingerprint=website_fingerprint,
                match_status=page.match_status,
                current=current,
                prior=prior,
            )
        )
    current_total, prior_total = _split_metric(site_rows, current_start)
    truncated = run.is_truncated or overlapping_truncation
    warnings = []
    if missing_dates:
        warnings.append("missing_site_total_dates")
    if truncated:
        warnings.append("overlapping_successful_sync_truncated")
    return SearchSnapshot(
        source_id=source.id,
        sync_id=run.id,
        health_status="degraded" if missing_dates or truncated else "healthy",
        baseline_status="ready" if not missing_dates and not truncated else "building",
        current_start=current_start,
        current_end=current_end,
        prior_start=prior_start,
        prior_end=prior_end,
        period_start=current_start,
        period_end=current_end,
        comparison_start=prior_start,
        comparison_end=prior_end,
        missing_dates=missing_dates,
        complete=not missing_dates,
        truncated=truncated,
        warnings=tuple(warnings),
        current=current_total,
        prior=prior_total,
        queries=tuple(sorted(query_snapshots, key=lambda item: item.query_fingerprint)),
        pages=tuple(sorted(page_snapshots, key=lambda item: item.url_fingerprint)),
    )


async def _assemble_website(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, as_of: datetime, profile_version: int
) -> WebsiteSnapshot | None:
    authority = await repository.get_website_authority(db, workspace_id, product_id, as_of)
    if authority is None:
        return None
    source, run, newer_failed = authority
    results, mappings = await repository.list_website_facts(
        db, workspace_id, product_id, source.id, run.id
    )
    pages = []
    page_fingerprints = {}
    for result, page in results:
        page_fingerprints[page.id] = page.url_fingerprint
        status = result.status.value if hasattr(result.status, "value") else str(result.status)
        crawl_status = (
            "blocked" if status == "blocked" else "failed" if status != "succeeded" else "succeeded"
        )
        pages.append(
            WebsitePageSnapshot(
                page_fingerprint=page.url_fingerprint,
                url=result.final_url or page.url,
                title=result.title,
                meta_description=result.meta_description,
                headings=_heading_values(result.headings),
                h1s=_heading_values(result.headings, 1),
                indexable=result.is_indexable,
                crawl_status=crawl_status,
                canonical_url=page.canonical_url,
                canonical_fingerprint=hashlib.sha256(page.canonical_url.encode()).hexdigest(),
            )
        )
    persisted_mapping_versions = {
        mapping.evidence.get("profile_version")
        for mapping in mappings
        if isinstance(mapping.evidence.get("profile_version"), int)
    }
    evidence_profile_version = run.summary.get(
        "profile_version",
        next(iter(persisted_mapping_versions)) if persisted_mapping_versions else profile_version,
    )
    if not isinstance(evidence_profile_version, int):
        evidence_profile_version = profile_version
    mapped = []
    for mapping in mappings:
        fingerprint = (
            page_fingerprints.get(mapping.page_id, f"missing:{mapping.capability_key}")
            if mapping.page_id is not None
            else f"missing:{mapping.capability_key}"
        )
        status_value = (
            mapping.status.value if hasattr(mapping.status, "value") else str(mapping.status)
        )
        mapped.append(
            CapabilityMappingSnapshot(
                capability_key=mapping.capability_key,
                page_fingerprint=fingerprint,
                profile_version=evidence_profile_version,
                coverage_score=Decimal(mapping.confidence_score) / 100,
                relevance_score=Decimal(mapping.relevance_score) / 100,
                status="complete" if status_value == "represented" else status_value,
            )
        )
    complete = run.pages_discovered == run.pages_succeeded and run.pages_failed == 0
    return WebsiteSnapshot(
        source_id=source.id,
        crawl_id=run.id,
        profile_version=evidence_profile_version,
        completed_at=_utc(run.completed_at) if run.completed_at is not None else None,
        health_status="healthy" if complete else "degraded",
        complete=complete,
        warnings=("newer_failed_crawl_ignored",) if newer_failed else (),
        pages=tuple(sorted(pages, key=lambda item: item.page_fingerprint)),
        capability_mappings=tuple(
            sorted(mapped, key=lambda item: (item.capability_key, item.page_fingerprint))
        ),
    )


async def assemble_detection_input(
    db: AsyncSession, workspace_id: UUID, product_id: UUID, as_of: datetime
) -> DetectionInputAssembly:
    as_of = _utc(as_of)
    product, profile = await repository.get_product_and_profile(db, workspace_id, product_id)
    if product is None:
        raise MissingProductError(
            f"Product {product_id} does not exist in workspace {workspace_id}."
        )
    if profile is None or profile.version is None or not profile.quarterly_objective:
        raise MissingApprovedProfileError("A complete approved product profile is required.")
    capabilities = tuple(
        sorted(
            (_profile_capability(value) for value in profile.important_capabilities),
            key=lambda capability: capability.capability_key,
        )
    )
    profile_snapshot = ProductProfileSnapshot(
        product_name=product.name,
        profile_id=profile.id,
        profile_version=profile.version,
        schema_version=str(profile.schema_version),
        completeness=Decimal(profile.completeness_score),
        objective=profile.quarterly_objective,
        audience=profile.primary_audience,
        conversion_action=(
            profile.primary_conversion_goal.value
            if profile.primary_conversion_goal is not None
            else profile.primary_conversion_url
        ),
        positioning=profile.main_value_proposition or profile.differentiation,
        capabilities=capabilities,
    )
    search = await _assemble_search(db, workspace_id, product_id, as_of, profile_snapshot)
    website = await _assemble_website(
        db, workspace_id, product_id, as_of, profile_snapshot.profile_version
    )
    window_start = as_of - timedelta(days=28)
    shipping_rows = await repository.list_shipping_rows(
        db, workspace_id, product_id, window_start, as_of
    )
    events_list = []
    for row in shipping_rows:
        capability_match = match_capability(
            " ".join(filter(None, (row.public_summary, row.user_benefit))), capabilities
        )
        events_list.append(
            ShippingEventSnapshot(
                event_id=row.id,
                event_key=row.dedupe_key,
                repository_id=row.repo_id,
                mapping_id=row.mapping_id,
                event_type=row.event_type,
                occurred_at=_utc(row.occurred_at),
                safe_summary=row.public_summary,
                user_benefit=row.user_benefit,
                evaluation_outcome=_shipping_outcome(row.outcome),
                public_worthiness_score=Decimal(row.public_worthiness_score),
                capability_key=capability_match.capability_key,
                capability_coverage_score=capability_match.coverage,
                capability_relevance_score=capability_match.relevance,
            )
        )
    events = tuple(events_list)
    history_rows = await repository.list_history(db, workspace_id, product_id)
    history = tuple(
        OpportunityHistoryItem(
            identity_fingerprint=opportunity.identity_fingerprint or opportunity.cooldown_dedup_key,
            action_family=opportunity.action_family,
            opportunity_type=opportunity.opportunity_type,
            status=opportunity.status,
            occurred_at=_utc(opportunity.created_at),
            dismissal_reason=feedback.reason_code if feedback is not None else None,
            cooldown_until=_utc(opportunity.cooldown_until),
            confidence_score=opportunity.confidence_score,
            strategic_fit_score=opportunity.strategic_fit_score,
            priority_score=opportunity.priority_score,
        )
        for opportunity, feedback in history_rows
        if opportunity.identity_fingerprint or opportunity.cooldown_dedup_key
    )
    detection_input = DetectionInput(
        schema_version="1",
        workspace_id=workspace_id,
        product_id=product_id,
        as_of=as_of,
        profile=profile_snapshot,
        search=search,
        website=website,
        shipping=ShippingSnapshot(window_start=window_start, window_end=as_of, events=events),
        history=OpportunityHistorySnapshot(opportunities=history),
    )
    return canonicalize_detection_input(detection_input)


_SORTED_COLLECTIONS = {
    "capabilities",
    "warnings",
    "missing_dates",
    "page_fingerprints",
    "queries",
    "pages",
    "capability_mappings",
    "events",
    "opportunities",
}


def _canonical(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {name: _canonical(item, name) for name, item in sorted(value.items())}
    if isinstance(value, list):
        values = [_canonical(item) for item in value]
        if key in _SORTED_COLLECTIONS:
            values.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        return values
    return value


def canonicalize_detection_input(detection_input: DetectionInput) -> DetectionInputAssembly:
    payload = _canonical(detection_input.model_dump(mode="json"))
    canonical_bytes = json.dumps(
        payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return DetectionInputAssembly(
        detection_input=detection_input,
        payload=payload,
        canonical_bytes=canonical_bytes,
        input_hash=hashlib.sha256(canonical_bytes).hexdigest(),
    )


async def persist_run_snapshot(
    db: SnapshotSession,
    *,
    workspace_id: UUID,
    product_id: UUID,
    operator_run_id: UUID,
    assembly: DetectionInputAssembly,
) -> OperatorRunSnapshot:
    expected = canonicalize_detection_input(assembly.detection_input)
    if (
        assembly.input_hash != expected.input_hash
        or assembly.canonical_bytes != expected.canonical_bytes
        or assembly.payload != expected.payload
    ):
        raise SnapshotHashMismatchError("Detection input payload does not match its SHA-256 hash.")
    if (
        assembly.detection_input.workspace_id != workspace_id
        or assembly.detection_input.product_id != product_id
    ):
        raise ValueError("Snapshot tenant does not match the detection input tenant.")
    existing: object | None = None
    if isinstance(db, AsyncSession):
        existing = await db.scalar(
            select(OperatorRunSnapshot).where(
                OperatorRunSnapshot.workspace_id == workspace_id,
                OperatorRunSnapshot.product_id == product_id,
                OperatorRunSnapshot.operator_run_id == operator_run_id,
            )
        )
    if existing is not None:
        if not isinstance(existing, OperatorRunSnapshot):
            raise SnapshotHashMismatchError("Run already has a different immutable snapshot.")
        try:
            stored_input = DetectionInput.model_validate(existing.input_payload)
            stored = canonicalize_detection_input(stored_input)
        except Exception as exc:
            raise SnapshotHashMismatchError(
                "Stored run snapshot is not canonical detection input."
            ) from exc
        if stored.input_hash != existing.input_hash or stored.input_hash != assembly.input_hash:
            raise SnapshotHashMismatchError("Run already has a different immutable snapshot.")
        return existing
    snapshot = OperatorRunSnapshot(
        workspace_id=workspace_id,
        product_id=product_id,
        operator_run_id=operator_run_id,
        schema_version=int(assembly.detection_input.schema_version),
        input_payload=assembly.payload,
        input_hash=assembly.input_hash,
        source_cutoff=assembly.detection_input.as_of,
        retention_expires_at=assembly.detection_input.as_of + timedelta(days=180),
    )
    db.add(snapshot)
    await db.flush()
    return snapshot
