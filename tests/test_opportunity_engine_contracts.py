from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String, UniqueConstraint

from app.db.base import Base
from app.modules.operator.enums import OpportunityType
from app.modules.operator.models import (  # noqa: F401
    OperatorRunSnapshot,
    OpportunityEvaluation,
    OpportunityEvidence,
    OpportunityFeedback,
    OpportunityStatusEvent,
)
from app.modules.operator.schemas import (
    DetectionInput,
    DetectorCandidate,
    DetectorEvaluation,
    EvidenceSnapshot,
    ScoreFeatures,
)


def _columns(table_name: str) -> set[str]:
    return set(Base.metadata.tables[table_name].c.keys())


def test_opportunity_engine_tables_and_additive_columns_are_registered() -> None:
    assert {
        "operator_run_snapshots",
        "opportunity_evaluations",
        "opportunity_evidence",
        "opportunity_feedback",
        "opportunity_status_events",
    } <= set(Base.metadata.tables)
    assert {
        "run_kind",
        "request_fingerprint",
        "input_fingerprint",
        "input_schema_version",
        "detector_suite_version",
        "quality_policy_version",
        "scoring_policy_version",
        "cooldown_policy_version",
        "as_of",
        "lease_owner",
        "lease_expires_at",
        "failure_category",
        "updated_at",
    } <= _columns("operator_runs")
    assert {
        "opportunity_type",
        "action_family",
        "subject_kind",
        "subject_keys",
        "identity_fingerprint",
        "observation_fingerprint",
        "detector_id",
        "detector_version",
        "confidence_score",
        "strategic_fit_score",
        "novelty_score",
        "priority_score",
        "score_version",
        "expected_metric",
        "effort_band",
        "lifecycle_version",
    } <= _columns("opportunities")


def test_new_children_use_product_scoped_composite_foreign_keys_and_candidate_keys() -> None:
    for table_name in (
        "operator_run_snapshots",
        "opportunity_evaluations",
        "opportunity_evidence",
        "opportunity_feedback",
        "opportunity_status_events",
    ):
        table = Base.metadata.tables[table_name]
        assert any(
            isinstance(constraint, UniqueConstraint)
            and [column.name for column in constraint.columns]
            == ["workspace_id", "product_id", "id"]
            for constraint in table.constraints
        )
        assert any(
            isinstance(constraint, ForeignKeyConstraint)
            and [column.name for column in constraint.columns][:2] == ["workspace_id", "product_id"]
            for constraint in table.constraints
        )

    opportunity_run_fks = [
        constraint
        for constraint in Base.metadata.tables["opportunities"].constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_opportunities_operator_run_id_tenant"
    ]
    assert len(opportunity_run_fks) == 1
    assert [column.name for column in opportunity_run_fks[0].columns] == [
        "workspace_id",
        "product_id",
        "operator_run_id",
    ]


def test_legacy_columns_remain_nullable_and_new_score_checks_exist() -> None:
    runs = Base.metadata.tables["operator_runs"]
    opportunities = Base.metadata.tables["opportunities"]
    for name in (
        "product_id",
        "request_fingerprint",
        "input_fingerprint",
        "input_schema_version",
        "detector_suite_version",
        "quality_policy_version",
        "scoring_policy_version",
        "cooldown_policy_version",
        "as_of",
    ):
        assert runs.c[name].nullable
    for name in (
        "opportunity_type",
        "identity_fingerprint",
        "confidence_score",
        "priority_score",
        "score_version",
    ):
        assert opportunities.c[name].nullable
    check_sql = " ".join(
        str(constraint.sqltext)
        for constraint in opportunities.constraints
        if isinstance(constraint, CheckConstraint)
    )
    assert "confidence_score BETWEEN 0 AND 100" in check_sql
    assert "priority_score BETWEEN 0 AND 100" in check_sql
    assert "superseded" in check_sql


def test_detector_types_match_the_six_roadmap_detectors() -> None:
    assert {item.value for item in OpportunityType} == {
        "high_impressions_low_ctr",
        "ranking_within_reach",
        "search_decline",
        "shipped_but_not_marketed",
        "search_demand_without_dedicated_content",
        "existing_page_needs_improvement",
    }


def test_internal_detection_contracts_are_frozen_forbid_extras_and_use_decimal_metrics() -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    contract = DetectionInput(
        schema_version="1",
        workspace_id=uuid4(),
        product_id=uuid4(),
        as_of=now,
        profile={"objective": "Increase qualified discovery"},
        search={"period_start": date(2026, 7, 1), "period_end": date(2026, 7, 27)},
        queries=[],
        pages=[],
        website={"pages": []},
        shipping={"events": []},
        history={"opportunities": []},
    )
    assert contract.as_of == now
    with pytest.raises(ValidationError):
        DetectionInput.model_validate({**contract.model_dump(), "raw_payload": {}})
    with pytest.raises(ValidationError):
        contract.schema_version = "2"  # type: ignore[misc]

    evidence = EvidenceSnapshot(
        evidence_key="search:query:one",
        source_type="search",
        source_record_type="query",
        observed_at=now,
        metrics={"impressions": Decimal("120.5")},
        public_safe_summary="A query received meaningful impressions.",
        confidence_score=Decimal("82.5"),
        freshness_status="fresh",
    )
    scores = ScoreFeatures(
        impact=Decimal("80"),
        confidence=Decimal("82.5"),
        effort=Decimal("20"),
        urgency=Decimal("60"),
        strategic_fit=Decimal("75"),
        novelty=Decimal("100"),
        priority=Decimal("91.2"),
    )
    candidate = DetectorCandidate(
        opportunity_type="ranking_within_reach",
        action_family="seo_page_update",
        subject_kind="query",
        subject_keys={"query": "example"},
        identity_fingerprint="a" * 64,
        observation_fingerprint="b" * 64,
        title="Improve ranking query",
        interpretation="The query is close to page one.",
        recommended_action="Improve the relevant page.",
        expected_metric={"name": "clicks", "direction": "increase"},
        effort_band="small",
        evidence=[evidence],
        scores=scores,
    )
    evaluation = DetectorEvaluation(
        detector_id="ranking_within_reach",
        detector_version="1",
        outcome="accepted",
        reason_codes=[],
        candidate=candidate,
    )
    assert evaluation.candidate is not None
    with pytest.raises(ValidationError):
        ScoreFeatures(
            impact=Decimal("101"),
            confidence=Decimal("50"),
            effort=Decimal("50"),
            urgency=Decimal("50"),
            strategic_fit=Decimal("50"),
            novelty=Decimal("50"),
            priority=Decimal("50"),
        )


def test_evidence_and_feedback_safety_constraints_are_metadata_visible() -> None:
    evidence = Base.metadata.tables["opportunity_evidence"]
    feedback = Base.metadata.tables["opportunity_feedback"]
    assert isinstance(evidence.c.public_safe_summary.type, String)
    assert evidence.c.public_safe_summary.type.length == 1000
    assert isinstance(feedback.c.comment.type, String)
    assert feedback.c.comment.type.length == 500
    assert any(
        isinstance(constraint, UniqueConstraint)
        and [column.name for column in constraint.columns]
        == ["workspace_id", "product_id", "evaluation_id", "evidence_key"]
        for constraint in evidence.constraints
    )
