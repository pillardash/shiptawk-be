from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.identity.models.users import User
from app.modules.integrations.models import (
    IntegrationConnection,
    github_raw_event_consumers,
    github_raw_events,
    normalized_events,
)
from app.modules.operator.detectors.opportunity_registry_detector import DETECTOR_REGISTRY
from app.modules.operator.models import (
    OperatorRun,
    OperatorRunSnapshot,
    Opportunity,
    OpportunityEvaluation,
    OpportunityEvidence,
    OpportunityFeedback,
    OpportunityStatusEvent,
    event_evaluations,
)
from app.modules.operator.schemas import OpportunityFeedbackCommand
from app.modules.operator.services.opportunity_detection_service import (
    DetectionPersistenceConflictError,
    persist_detection,
    replay_run_snapshot,
)
from app.modules.operator.services.opportunity_feedback_service import (
    FeedbackIdempotencyConflictError,
    append_feedback,
    dismiss_opportunity,
)
from app.modules.operator.services.opportunity_snapshot_service import (
    DetectionInputAssembly,
    MissingApprovedProfileError,
    MissingProductError,
    SnapshotHashMismatchError,
    assemble_detection_input,
    persist_run_snapshot,
)
from app.modules.products.enums.product_profile_enum import ProductProfileStatus
from app.modules.products.enums.website_intelligence_enum import (
    WebsiteCapabilityMappingStatus,
    WebsiteSourceStatus,
)
from app.modules.products.models import (
    Product,
    ProductProfile,
    WebsiteCapabilityMapping,
    WebsiteCrawlResult,
    WebsiteCrawlRun,
    WebsitePage,
    WebsiteSource,
    product_repositories,
)
from app.modules.repos.models import repos
from app.modules.search_intelligence.models import (
    SearchDailyMetric,
    SearchPage,
    SearchProviderProperty,
    SearchQuery,
    SearchSource,
    SearchSyncRun,
)
from app.modules.workspaces.models import Workspace, WorkspaceMembership, WorkspaceRole

NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)
CUTOFF = NOW.date()


@pytest.fixture
async def snapshot_db() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    await engine.dispose()


@dataclass(frozen=True)
class Graph:
    user_id: UUID
    workspace_id: UUID
    product_id: UUID
    profile_id: UUID
    repo_id: UUID
    mapping_id: UUID
    website_source_id: UUID
    crawl_id: UUID
    website_page_id: UUID
    search_source_id: UUID
    search_run_id: UUID
    query_id: UUID
    search_page_id: UUID
    opportunity_id: UUID


async def seed_graph(
    db: AsyncSession,
    *,
    label: str = "target",
    site_clicks: int = 2,
    reverse_metrics: bool = False,
) -> Graph:
    user = User(email=f"{label}-{uuid4()}@example.com")
    workspace = Workspace(name=f"{label} workspace")
    db.add_all([user, workspace])
    await db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.owner, is_active=True
        )
    )
    product = Product(workspace_id=workspace.id, user_id=user.id, name=f"{label} product")
    db.add(product)
    await db.flush()
    profile = ProductProfile(
        workspace_id=workspace.id,
        product_id=product.id,
        status="approved",
        version=2,
        draft_revision=1,
        schema_version=1,
        primary_audience="Product teams",
        main_value_proposition=f"{label} positioning",
        primary_conversion_goal="signup",
        important_capabilities=["automation"],
        quarterly_objective=f"Grow {label} signups",
        completeness_score=100,
        approved_at=NOW - timedelta(days=60),
        approved_by=user.id,
    )
    db.add(profile)
    repo_id, mapping_id = uuid4(), uuid4()
    await db.execute(
        insert(repos).values(
            id=repo_id,
            workspace_id=workspace.id,
            user_id=user.id,
            repo_name=label,
            repo_full_name=f"acme/{label}",
            tracked_branch="main",
            is_tracked=True,
        )
    )
    await db.execute(
        insert(product_repositories).values(
            id=mapping_id,
            workspace_id=workspace.id,
            product_id=product.id,
            repo_id=repo_id,
            repo_role="backend",
            is_primary_repo=True,
        )
    )

    website = WebsiteSource(
        workspace_id=workspace.id,
        product_id=product.id,
        base_url=f"https://{label}.example/",
        normalized_base_url=f"https://{label}.example/",
        status="active",
        created_by=user.id,
    )
    db.add(website)
    await db.flush()
    crawl = WebsiteCrawlRun(
        workspace_id=workspace.id,
        product_id=product.id,
        source_id=website.id,
        status="succeeded",
        trigger="manual",
        idempotency_key=f"crawl-{label}",
        request_fingerprint="c" * 64,
        page_limit=1,
        pages_discovered=1,
        pages_succeeded=1,
        pages_failed=0,
        summary={"profile_version": 1},
        started_at=NOW - timedelta(days=3),
        completed_at=NOW - timedelta(days=2),
    )
    db.add(crawl)
    await db.flush()
    website_page = WebsitePage(
        workspace_id=workspace.id,
        product_id=product.id,
        source_id=website.id,
        first_discovered_run_id=crawl.id,
        url=f"https://{label}.example/features",
        canonical_url=f"https://{label}.example/features",
        url_fingerprint=f"web-{label}",
        status="active",
    )
    db.add(website_page)
    await db.flush()
    db.add_all(
        [
            WebsiteCrawlResult(
                workspace_id=workspace.id,
                product_id=product.id,
                source_id=website.id,
                crawl_run_id=crawl.id,
                page_id=website_page.id,
                status="succeeded",
                final_url=website_page.url,
                http_status=200,
                title=f"{label} title",
                headings=[{"level": 1, "text": "Features"}, {"level": 2, "text": "Details"}],
                content_fingerprint="d" * 64,
                is_indexable=None,
                fetched_at=NOW - timedelta(days=2),
            ),
            WebsiteCapabilityMapping(
                workspace_id=workspace.id,
                product_id=product.id,
                source_id=website.id,
                crawl_run_id=crawl.id,
                page_id=website_page.id,
                capability_key="automation",
                capability_name="Automation",
                status="represented",
                relevance_score=80,
                confidence_score=90,
                evidence={"profile_version": 1},
                mapping_version="v1",
            ),
        ]
    )

    connection = IntegrationConnection(
        workspace_id=workspace.id,
        provider="google_search",
        external_account_id=label,
        idempotency_key=f"connection-{label}",
        status="active",
    )
    db.add(connection)
    await db.flush()
    prop = SearchProviderProperty(
        workspace_id=workspace.id,
        integration_connection_id=connection.id,
        provider="google_search",
        provider_property_id=f"sc-domain:{label}.example",
        display_name=label,
        property_type="domain",
        permission_level="siteOwner",
        compatibility_status="compatible",
        first_discovered_at=NOW - timedelta(days=70),
        last_seen_at=NOW,
    )
    db.add(prop)
    await db.flush()
    source = SearchSource(
        workspace_id=workspace.id,
        product_id=product.id,
        integration_connection_id=connection.id,
        property_id=prop.id,
        website_source_id=website.id,
        provider="google_search",
        status="active",
        selected_at=NOW - timedelta(days=70),
        latest_successful_data_date=CUTOFF,
        created_by=user.id,
    )
    db.add(source)
    await db.flush()
    run = SearchSyncRun(
        workspace_id=workspace.id,
        product_id=product.id,
        source_id=source.id,
        trigger="manual",
        status="succeeded",
        idempotency_key=f"search-{label}",
        request_fingerprint="s" * 64,
        requested_start_date=CUTOFF - timedelta(days=55),
        requested_end_date=CUTOFF,
        progress_date=CUTOFF,
        policy_version="v1",
        schema_version=1,
        is_complete=True,
        is_truncated=False,
        started_at=NOW - timedelta(hours=2),
        completed_at=NOW - timedelta(hours=1),
    )
    db.add(run)
    query = SearchQuery(
        workspace_id=workspace.id,
        product_id=product.id,
        source_id=source.id,
        query_text=f"{label} query",
        query_fingerprint=f"query-{label}",
    )
    search_page = SearchPage(
        workspace_id=workspace.id,
        product_id=product.id,
        source_id=source.id,
        original_url=website_page.url,
        normalized_url=website_page.url,
        url_fingerprint=f"search-{label}",
        website_source_id=website.id,
        website_page_id=website_page.id,
        match_status="matched",
        match_method="exact",
        match_policy_version="v1",
    )
    db.add_all([query, search_page])
    await db.flush()
    metrics = [
        SearchDailyMetric(
            workspace_id=workspace.id,
            product_id=product.id,
            source_id=source.id,
            metric_date=CUTOFF - timedelta(days=offset),
            search_type="web",
            grain="site_total",
            clicks=site_clicks,
            impressions=10,
            average_position=Decimal(offset % 2 + 2),
        )
        for offset in range(56)
    ]
    if reverse_metrics:
        metrics.reverse()
    db.add_all(metrics)
    db.add_all(
        [
            SearchDailyMetric(
                workspace_id=workspace.id,
                product_id=product.id,
                source_id=source.id,
                query_id=query.id,
                page_id=search_page.id,
                metric_date=CUTOFF,
                search_type="web",
                grain="query_page",
                clicks=30,
                impressions=100,
                average_position=Decimal("4"),
            ),
            SearchDailyMetric(
                workspace_id=workspace.id,
                product_id=product.id,
                source_id=source.id,
                query_id=query.id,
                page_id=search_page.id,
                metric_date=CUTOFF - timedelta(days=28),
                search_type="web",
                grain="query_page",
                clicks=7,
                impressions=50,
                average_position=Decimal("10"),
            ),
        ]
    )

    raw_id, consumer_id, normalized_id = uuid4(), uuid4(), uuid4()
    await db.execute(
        insert(github_raw_events).values(
            id=raw_id,
            github_delivery_id=f"delivery-{label}-{uuid4()}",
            event_name="release",
            repo_full_name=f"acme/{label}",
            payload_ciphertext="encrypted",
            payload_key_version="v1",
        )
    )
    await db.execute(
        insert(github_raw_event_consumers).values(
            id=consumer_id,
            workspace_id=workspace.id,
            raw_event_id=raw_id,
            repo_id=repo_id,
            user_id=user.id,
            processing_state="processed",
        )
    )
    await db.execute(
        insert(normalized_events).values(
            id=normalized_id,
            workspace_id=workspace.id,
            user_id=user.id,
            raw_event_id=raw_id,
            repo_id=repo_id,
            event_type="release",
            repo_full_name=f"acme/{label}",
            title="Private raw title",
            description="Private raw description",
            url="https://github.example/private",
            occurred_at=NOW - timedelta(days=2),
            dedupe_key=f"release:{label}",
            touched_paths=["secret.py"],
        )
    )
    await db.execute(
        insert(event_evaluations).values(
            id=uuid4(),
            workspace_id=workspace.id,
            user_id=user.id,
            normalized_event_id=normalized_id,
            repo_id=repo_id,
            public_worthiness_score=88,
            is_public_worthy=True,
            rationale="internal rationale",
            failed_safety_checks=[],
            outcome="generate",
            dimension_scores={},
            internal_summary="internal only",
            public_summary=f"{label} shipped",
            user_benefit="Faster work",
        )
    )

    opportunity = Opportunity(
        workspace_id=workspace.id,
        product_id=product.id,
        title=f"{label} opportunity",
        evidence_ids=[],
        interpretation="signal",
        recommendation="act",
        confidence="high",
        missing_information=[],
        approval_level="review",
        measurement_window="28d",
        stop_conditions=[],
        impact_score=70,
        effort_score=20,
        urgency_score=60,
        confidence_score=80,
        strategic_fit_score=75,
        priority_score=77,
        status="dismissed",
        cooldown_dedup_key=f"cooldown-{label}",
        identity_fingerprint=f"identity-{label}",
        cooldown_until=NOW + timedelta(days=7),
        provenance={},
        created_by_actor_id=user.id,
        created_at=NOW - timedelta(days=5),
        opportunity_type="high_impressions_low_ctr",
        action_family="seo_snippet_update",
    )
    db.add(opportunity)
    await db.flush()
    db.add(
        OpportunityFeedback(
            workspace_id=workspace.id,
            product_id=product.id,
            opportunity_id=opportunity.id,
            actor_id=user.id,
            idempotency_key=f"seed-feedback-{label}",
            request_fingerprint="f" * 64,
            reason_code="not_relevant",
            evaluation_eligible=True,
            created_at=NOW - timedelta(days=1),
        )
    )
    await db.flush()
    return Graph(
        user.id,
        workspace.id,
        product.id,
        profile.id,
        repo_id,
        mapping_id,
        website.id,
        crawl.id,
        website_page.id,
        source.id,
        run.id,
        query.id,
        search_page.id,
        opportunity.id,
    )


async def assembled(db: AsyncSession, graph: Graph) -> DetectionInputAssembly:
    return await assemble_detection_input(db, graph.workspace_id, graph.product_id, NOW)


@pytest.mark.asyncio
async def test_assembled_shipping_snapshot_reaches_shipped_detector(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db, label="automation")
        profile = await db.get(ProductProfile, graph.profile_id)
        crawl = await db.get(WebsiteCrawlRun, graph.crawl_id)
        mapping = await db.scalar(
            select(WebsiteCapabilityMapping).where(
                WebsiteCapabilityMapping.crawl_run_id == graph.crawl_id
            )
        )
        assert profile and crawl and mapping
        profile.important_capabilities = cast(
            Any,
            [
                {
                    "capability_key": "automation",
                    "name": "Workflow automation",
                    "description": "Automate recurring work",
                }
            ],
        )
        crawl.summary = {"profile_version": 2}
        crawl.completed_at = NOW - timedelta(days=1)
        mapping.status = WebsiteCapabilityMappingStatus.partial
        mapping.evidence = {"profile_version": 2}
        mapping.confidence_score = 60
        mapping.relevance_score = 20
        await db.execute(
            update(event_evaluations).values(
                public_summary="Workflow automation shipped",
                user_benefit="Automate recurring work",
            )
        )
        await db.commit()

        detection_input = (await assembled(db, graph)).detection_input
        detector = next(
            item[2] for item in DETECTOR_REGISTRY if item[0] == "shipped_but_not_marketed"
        )
        evaluations = detector(detection_input)

    event_snapshot = detection_input.shipping.events[0]
    assert event_snapshot.capability_key == "automation"
    assert event_snapshot.capability_coverage_score >= Decimal(".60")
    assert any(item.outcome.value == "accepted" for item in evaluations)


@pytest.mark.asyncio
async def test_assembled_query_snapshot_reaches_content_gap_detector(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db, label="reports")
        profile = await db.get(ProductProfile, graph.profile_id)
        product = await db.get(Product, graph.product_id)
        crawl = await db.get(WebsiteCrawlRun, graph.crawl_id)
        query = await db.get(SearchQuery, graph.query_id)
        search_page = await db.get(SearchPage, graph.search_page_id)
        assert profile and product and crawl and query and search_page
        product.name = "Atlas"
        profile.important_capabilities = cast(
            Any, [{"capability_key": "reports", "name": "Automated reports"}]
        )
        crawl.summary = {"profile_version": 2}
        query.query_text = "automated reports for product teams"
        search_page.match_status = "unmatched"
        search_page.website_source_id = None
        search_page.website_page_id = None
        await db.commit()

        detection_input = (await assembled(db, graph)).detection_input
        detector = next(
            item[2]
            for item in DETECTOR_REGISTRY
            if item[0] == "search_demand_without_dedicated_content"
        )
        evaluations = detector(detection_input)

    query_snapshot = detection_input.search.queries[0]  # type: ignore[union-attr]
    assert query_snapshot.profile_relevance >= Decimal(".20")
    assert not query_snapshot.branded and not query_snapshot.navigational
    assert any(item.outcome.value == "accepted" for item in evaluations)


@pytest.mark.asyncio
async def test_detection_persists_every_evaluation_and_replays_idempotently(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db, label="detection")
        input_assembly = await assembled(db, graph)
        run = OperatorRun(
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            run_kind="opportunity_detection",
            trigger="manual",
            status="running",
            idempotency_key="phase4-detection",
            analyzer_version="v1",
            provenance={},
            summary={},
            created_by_actor_id=graph.user_id,
        )
        db.add(run)
        await db.commit()

        first = await persist_detection(
            db,
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            operator_run_id=run.id,
            assembly=input_assembly,
        )
        second = await persist_detection(
            db,
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            operator_run_id=run.id,
            assembly=input_assembly,
        )
        assert len(first) >= 6
        assert [item.id for item in second] == [item.id for item in first]
        assert len(
            list(
                await db.scalars(
                    select(OpportunityEvaluation).where(
                        OpportunityEvaluation.operator_run_id == run.id
                    )
                )
            )
        ) == len(first)
        evidence = list(await db.scalars(select(OpportunityEvidence)))
        for item in evidence:
            assert item.workspace_id == graph.workspace_id
            assert len(item.source_fingerprint) == 64
            assert "raw" not in item.period
            assert "payload" not in item.metrics

        changed = input_assembly.model_copy(update={"input_hash": "0" * 64})
        with pytest.raises(DetectionPersistenceConflictError):
            await persist_detection(
                db,
                workspace_id=graph.workspace_id,
                product_id=graph.product_id,
                operator_run_id=run.id,
                assembly=changed,
            )


@pytest.mark.asyncio
async def test_completed_run_snapshot_replay_matches_persisted_evaluations(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db, label="replay")
        input_assembly = await assembled(db, graph)
        run = OperatorRun(
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            run_kind="opportunity_detection",
            trigger="manual",
            status="running",
            idempotency_key="phase4-replay",
            analyzer_version="v1",
            provenance={},
            summary={},
            created_by_actor_id=graph.user_id,
        )
        db.add(run)
        await db.flush()
        snapshot = await persist_run_snapshot(
            db,
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            operator_run_id=run.id,
            assembly=input_assembly,
        )
        rows = await persist_detection(
            db,
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            operator_run_id=run.id,
            assembly=input_assembly,
        )

        persisted = tuple(
            {
                "detector_id": row.detector_id,
                "detector_version": row.detector_version,
                "outcome": row.outcome,
                "reason_codes": row.reason_codes,
                "identity_fingerprint": row.identity_fingerprint,
                "observation_fingerprint": row.observation_fingerprint,
                "candidate_snapshot": row.candidate_snapshot,
                "priority_score": str(row.priority_score)
                if row.priority_score is not None
                else None,
                "evidence": (
                    row.candidate_snapshot.get("evidence", [])
                    if row.candidate_snapshot is not None
                    else []
                ),
            }
            for row in rows
        )
        assert replay_run_snapshot(snapshot) == persisted
        assert replay_run_snapshot(snapshot) == replay_run_snapshot(snapshot)

        snapshot.input_hash = "0" * 64
        with pytest.raises(SnapshotHashMismatchError):
            replay_run_snapshot(snapshot)
        snapshot.input_hash = input_assembly.input_hash
        snapshot.input_payload = {**snapshot.input_payload, "schema_version": "tampered"}
        with pytest.raises(SnapshotHashMismatchError):
            replay_run_snapshot(snapshot)


@pytest.mark.asyncio
async def test_feedback_and_dismissal_are_scoped_append_only_and_idempotent(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db, label="feedback")
        opportunity = await db.get(Opportunity, graph.opportunity_id)
        assert opportunity is not None
        opportunity_id = opportunity.id
        opportunity.status = "open"
        await db.commit()
        standalone = OpportunityFeedbackCommand(
            opportunity_id=opportunity.id,
            reason_code="useful",
            usefulness=5,
        )
        first = await append_feedback(
            db,
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            actor_id=graph.user_id,
            idempotency_key="standalone-feedback",
            command=standalone,
        )
        replay = await append_feedback(
            db,
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            actor_id=graph.user_id,
            idempotency_key="standalone-feedback",
            command=standalone,
        )
        assert replay.id == first.id
        assert opportunity.status == "open"
        with pytest.raises(FeedbackIdempotencyConflictError):
            await append_feedback(
                db,
                workspace_id=graph.workspace_id,
                product_id=graph.product_id,
                actor_id=graph.user_id,
                idempotency_key="standalone-feedback",
                command=standalone.model_copy(update={"comment": "different"}),
            )

        dismissed = await dismiss_opportunity(
            db,
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            actor_id=graph.user_id,
            idempotency_key="dismiss-feedback",
            command=OpportunityFeedbackCommand(
                opportunity_id=opportunity_id,
                reason_code="not_relevant",
                usefulness=5,
            ),
        )
        assert dismissed.reason_code == "not_relevant"
        opportunity = await db.get(Opportunity, opportunity_id)
        assert opportunity is not None
        assert opportunity.status == "dismissed"
        events = list(
            await db.scalars(
                select(OpportunityStatusEvent).where(
                    OpportunityStatusEvent.opportunity_id == opportunity.id
                )
            )
        )
        assert len(events) == 1
        assert events[0].actor_id == graph.user_id

        history = await assemble_detection_input(
            db, graph.workspace_id, graph.product_id, NOW + timedelta(days=1)
        )
        item = next(
            entry
            for entry in history.detection_input.history.opportunities
            if entry.identity_fingerprint == opportunity.identity_fingerprint
        )
        assert item.dismissal_reason == "not_relevant"


@pytest.mark.asyncio
async def test_assemble_snapshot_reads_only_requested_tenant_and_product(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        target = await seed_graph(db, label="target")
        await seed_graph(db, label="other-workspace", site_clicks=99)
        other_product = Product(
            workspace_id=target.workspace_id,
            user_id=target.user_id,
            name="same workspace distractor",
        )
        db.add(other_product)
        await db.flush()
        db.add(
            ProductProfile(
                workspace_id=target.workspace_id,
                product_id=other_product.id,
                status="approved",
                version=1,
                draft_revision=1,
                schema_version=1,
                important_capabilities=["foreign"],
                quarterly_objective="Foreign objective",
                completeness_score=100,
                approved_at=NOW,
                approved_by=target.user_id,
            )
        )
        await db.flush()
        result = await assembled(db, target)
    rendered = str(result.payload)
    assert result.detection_input.profile.objective == "Grow target signups"
    assert result.detection_input.search is not None
    assert result.detection_input.search.current is not None
    assert result.detection_input.search.current.clicks == 56
    assert "other-workspace" not in rendered and "Foreign objective" not in rendered
    assert (
        len(result.detection_input.shipping.events)
        == len(result.detection_input.history.opportunities)
        == 1
    )


@pytest.mark.asyncio
async def test_search_snapshot_uses_site_totals_and_exact_weighted_periods(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db)
        result = await assembled(db, graph)
    search = result.detection_input.search
    assert search is not None
    assert search.current is not None and search.prior is not None
    assert (search.current_start, search.current_end, search.prior_start, search.prior_end) == (
        CUTOFF - timedelta(days=27),
        CUTOFF,
        CUTOFF - timedelta(days=55),
        CUTOFF - timedelta(days=28),
    )
    assert search.current.clicks == search.prior.clicks == 56
    assert search.current.impressions == search.prior.impressions == 280
    assert search.current.average_position == search.prior.average_position == Decimal("2.5")
    assert search.queries[0].current.clicks == 30 and search.queries[0].prior.clicks == 7
    assert search.queries[0].matched_page_fingerprint == "search-target"
    assert search.pages[0].website_page_fingerprint == "web-target"
    assert search.missing_dates == ()


@pytest.mark.asyncio
async def test_search_snapshot_reports_missing_date_and_truncation_and_ignores_failed_authority(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db)
        missing = await db.scalar(
            select(SearchDailyMetric).where(
                SearchDailyMetric.source_id == graph.search_source_id,
                SearchDailyMetric.grain == "site_total",
                SearchDailyMetric.metric_date == CUTOFF - timedelta(days=10),
            )
        )
        assert missing is not None
        await db.delete(missing)
        db.add(
            SearchSyncRun(
                workspace_id=graph.workspace_id,
                product_id=graph.product_id,
                source_id=graph.search_source_id,
                trigger="manual",
                status="failed",
                idempotency_key="failed-authority",
                request_fingerprint="f" * 64,
                requested_start_date=CUTOFF - timedelta(days=55),
                requested_end_date=CUTOFF,
                policy_version="v1",
                schema_version=1,
                is_complete=False,
                is_truncated=True,
                started_at=NOW - timedelta(minutes=20),
                completed_at=NOW - timedelta(minutes=10),
                failure_category="provider",
            )
        )
        overlap = SearchSyncRun(
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            source_id=graph.search_source_id,
            trigger="manual",
            status="succeeded",
            idempotency_key="truncated-overlap",
            request_fingerprint="t" * 64,
            requested_start_date=CUTOFF - timedelta(days=5),
            requested_end_date=CUTOFF,
            policy_version="v1",
            schema_version=1,
            is_complete=False,
            is_truncated=True,
            started_at=NOW - timedelta(days=1),
            completed_at=NOW - timedelta(hours=3),
        )
        db.add(overlap)
        await db.flush()
        result = await assembled(db, graph)
    search = result.detection_input.search
    assert search is not None and search.sync_id == graph.search_run_id
    assert search.missing_dates == (CUTOFF - timedelta(days=10),)
    assert (
        search.truncated
        and search.health_status == "degraded"
        and search.baseline_status == "building"
    )
    assert set(search.warnings) == {
        "missing_site_total_dates",
        "overlapping_successful_sync_truncated",
    }


@pytest.mark.asyncio
async def test_website_snapshot_uses_latest_succeeded_run_and_preserves_profile_mismatch_and_unknown_indexability(  # noqa: E501
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db)
        db.add(
            WebsiteCrawlRun(
                workspace_id=graph.workspace_id,
                product_id=graph.product_id,
                source_id=graph.website_source_id,
                status="failed",
                trigger="manual",
                idempotency_key="new-failed",
                request_fingerprint="x" * 64,
                page_limit=1,
                pages_discovered=0,
                pages_succeeded=0,
                pages_failed=0,
                summary={},
                started_at=NOW - timedelta(minutes=30),
                completed_at=NOW - timedelta(minutes=20),
                error_code="timeout",
            )
        )
        await db.flush()
        result = await assembled(db, graph)
    website = result.detection_input.website
    assert website is not None and website.crawl_id == graph.crawl_id
    assert website.profile_version == 1 != result.detection_input.profile.profile_version
    assert website.pages[0].indexable is None  # type: ignore[union-attr]
    assert website.pages[0].headings == (  # type: ignore[union-attr]
        "Features",
        "Details",
    ) and website.pages[0].h1s == (  # type: ignore[union-attr]
        "Features",
    )
    assert website.warnings == ("newer_failed_crawl_ignored",)


@pytest.mark.asyncio
async def test_shipping_snapshot_filters_mapping_window_and_omits_raw_fields(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db)
        for outcome, days, mapped in (
            ("ignore", 3, True),
            ("hold", 4, True),
            ("generate", 40, True),
            ("generate", 2, False),
        ):
            repo_id = graph.repo_id if mapped else uuid4()
            if not mapped:
                await db.execute(
                    insert(repos).values(
                        id=repo_id,
                        workspace_id=graph.workspace_id,
                        user_id=graph.user_id,
                        repo_name="unmapped",
                        repo_full_name="acme/unmapped",
                        is_tracked=True,
                    )
                )
            raw_id, normalized_id = uuid4(), uuid4()
            await db.execute(
                insert(github_raw_events).values(
                    id=raw_id,
                    github_delivery_id=str(uuid4()),
                    event_name="push",
                    repo_full_name="acme/repo",
                    payload_ciphertext="secret",
                    payload_key_version="v1",
                )
            )
            await db.execute(
                insert(github_raw_event_consumers).values(
                    id=uuid4(),
                    workspace_id=graph.workspace_id,
                    raw_event_id=raw_id,
                    repo_id=repo_id,
                    user_id=graph.user_id,
                    processing_state="processed",
                )
            )
            await db.execute(
                insert(normalized_events).values(
                    id=normalized_id,
                    workspace_id=graph.workspace_id,
                    user_id=graph.user_id,
                    raw_event_id=raw_id,
                    repo_id=repo_id,
                    event_type="push",
                    repo_full_name="acme/repo",
                    title="raw",
                    description="raw secret",
                    url="private",
                    occurred_at=NOW - timedelta(days=days),
                    dedupe_key=f"{outcome}-{days}-{mapped}",
                    touched_paths=["private.py"],
                )
            )
            await db.execute(
                insert(event_evaluations).values(
                    id=uuid4(),
                    workspace_id=graph.workspace_id,
                    user_id=graph.user_id,
                    normalized_event_id=normalized_id,
                    repo_id=repo_id,
                    public_worthiness_score=50,
                    is_public_worthy=outcome == "generate",
                    rationale="private rationale",
                    failed_safety_checks=[],
                    outcome=outcome,
                    dimension_scores={},
                    public_summary=f"safe {outcome}",
                )
            )
        result = await assembled(db, graph)
    events = result.detection_input.shipping.events
    assert [item.evaluation_outcome for item in events] == ["review", "ignore", "generate"]
    assert all(item.mapping_id == graph.mapping_id for item in events)
    assert not (
        {"title", "description", "url", "touched_paths", "rationale"}
        & set(result.payload["shipping"]["events"][0])  # type: ignore[index]
    )


@pytest.mark.asyncio
async def test_history_uses_latest_feedback_and_product_scope(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db)
        db.add(
            OpportunityFeedback(
                workspace_id=graph.workspace_id,
                product_id=graph.product_id,
                opportunity_id=graph.opportunity_id,
                actor_id=graph.user_id,
                idempotency_key="latest-feedback",
                request_fingerprint="a" * 64,
                reason_code="already_done",
                evaluation_eligible=True,
                created_at=NOW,
            )
        )
        await db.flush()
        result = await assembled(db, graph)
    assert len(result.detection_input.history.opportunities) == 1
    assert result.detection_input.history.opportunities[0].dismissal_reason == "already_done"


@pytest.mark.asyncio
async def test_snapshot_hash_is_insertion_order_stable_but_changes_with_fact(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        first = await seed_graph(db, label="first", reverse_metrics=False)
        second = await seed_graph(db, label="second", reverse_metrics=True)
        # Align immutable tenant/profile text so only insertion order differs.
        second_product = await db.get(Product, second.product_id)
        second_profile = await db.get(ProductProfile, second.profile_id)
        assert second_product and second_profile
        second_profile.quarterly_objective = "Grow first signups"
        second_profile.main_value_proposition = "first positioning"
        a = await assembled(db, first)
        b = await assembled(db, second)
        normalized_b = b.detection_input.model_copy(
            update={
                "workspace_id": first.workspace_id,
                "product_id": first.product_id,
                "profile": b.detection_input.profile.model_copy(
                    update={"profile_id": first.profile_id}
                ),
                "search": b.detection_input.search.model_copy(
                    update={"source_id": first.search_source_id, "sync_id": first.search_run_id}
                )
                if b.detection_input.search
                else None,
                "website": b.detection_input.website.model_copy(
                    update={"source_id": first.website_source_id, "crawl_id": first.crawl_id}
                )
                if b.detection_input.website
                else None,
            }
        )
        from app.modules.operator.services.opportunity_snapshot_service import (
            canonicalize_detection_input,
        )

        assert (
            canonicalize_detection_input(normalized_b).input_hash != a.input_hash
        )  # remaining fact identities differ
        original = await assembled(db, first)
        rows = list(
            await db.scalars(
                select(SearchDailyMetric).where(
                    SearchDailyMetric.source_id == first.search_source_id,
                    SearchDailyMetric.grain == "site_total",
                )
            )
        )
        rows.reverse()
        stable = await assembled(db, first)
        assert original.input_hash == stable.input_hash
        rows[0].clicks += 1
        changed = await assembled(db, first)
        assert changed.input_hash != original.input_hash


@pytest.mark.asyncio
async def test_persist_snapshot_flushes_without_commit_and_enforces_unique_run(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db)
        assembly = await assembled(db, graph)
        run = OperatorRun(
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            run_kind="opportunity_detection",
            trigger="manual",
            status="running",
            idempotency_key="operator-run",
            analyzer_version="v1",
            provenance={},
            summary={},
            created_by_actor_id=graph.user_id,
        )
        db.add(run)
        await db.flush()
        snapshot = await persist_run_snapshot(
            db,
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            operator_run_id=run.id,
            assembly=assembly,
        )
        assert (
            snapshot.id is not None
            and (await db.scalar(select(OperatorRunSnapshot.id))) == snapshot.id
        )
        replay = await persist_run_snapshot(
            db,
            workspace_id=graph.workspace_id,
            product_id=graph.product_id,
            operator_run_id=run.id,
            assembly=assembly,
        )
        assert replay.id == snapshot.id
        await db.rollback()
    async with snapshot_db() as db:
        assert await db.scalar(select(OperatorRunSnapshot.id)) is None


@pytest.mark.asyncio
async def test_missing_product_profile_and_inactive_optional_sources(
    snapshot_db: async_sessionmaker[AsyncSession],
) -> None:
    async with snapshot_db() as db:
        graph = await seed_graph(db)
        with pytest.raises(MissingProductError):
            await assemble_detection_input(db, graph.workspace_id, uuid4(), NOW)
        profile = await db.get(ProductProfile, graph.profile_id)
        assert profile is not None
        profile.quarterly_objective = None
        without_objective = await assembled(db, graph)
        assert without_objective.detection_input.profile.objective is None
        profile.status = ProductProfileStatus.superseded
        await db.flush()
        with pytest.raises(MissingApprovedProfileError):
            await assembled(db, graph)
        profile.status = ProductProfileStatus.approved
        search = await db.get(SearchSource, graph.search_source_id)
        website = await db.get(WebsiteSource, graph.website_source_id)
        assert search and website
        search.status = "disconnected"
        search.disconnected_at = NOW
        website.status = WebsiteSourceStatus.disconnected
        website.disconnected_at = NOW
        await db.flush()
        result = await assembled(db, graph)
    assert result.detection_input.search is None and result.detection_input.website is None
