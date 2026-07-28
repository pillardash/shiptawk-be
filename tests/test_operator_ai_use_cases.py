from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint

from app.db.base import Base
from app.modules.operator import models as operator_models  # noqa: F401
from app.modules.operator.enums.operator_enum import ActionFamily
from app.modules.operator.models import ActionRiskReview, ApprovalRequest, PlanAction, PreparedAsset
from app.modules.operator.policies.ai_output_policy import (
    EvidenceCandidate,
    output_type_for_action_family,
    validate_asset_claims,
    validate_evidence_selection,
    validate_output_compatibility,
)
from app.modules.operator.policies.approval_policy import action_has_required_approval
from app.modules.operator.policies.risk_policy import final_risk_outcome
from app.modules.operator.prompts.prepared_asset_prompt import build_prepared_asset_prompt
from app.modules.operator.prompts.recommendation_prompt import build_recommendation_prompt
from app.modules.operator.prompts.risk_review_prompt import build_risk_review_prompt
from app.modules.operator.schemas.ai_output_schema import (
    NoAsset,
    NoRecommendation,
    PreparedAssetOutput,
    RecommendationOutput,
    RiskReviewOutput,
)


def test_strict_discriminated_outputs_accept_valid_no_results() -> None:
    recommendation: NoRecommendation = TypeAdapter(NoRecommendation).validate_python(
        {"kind": "no_recommendation", "reason": "Insufficient evidence", "evidence_ids": []}
    )
    asset: NoAsset = TypeAdapter(NoAsset).validate_python(
        {"kind": "no_asset", "reason": "Claims are not supportable"}
    )
    risk = TypeAdapter(RiskReviewOutput).validate_python({"outcome": "pass", "findings": []})

    assert recommendation.kind == "no_recommendation"
    assert asset.kind == "no_asset"
    assert risk.outcome == "pass"


def test_outputs_forbid_unknown_fields_and_require_typed_asset_content() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(RecommendationOutput).validate_python(
            {
                "kind": "no_recommendation",
                "reason": "No basis",
                "evidence_ids": [],
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        TypeAdapter(PreparedAssetOutput).validate_python(
            {"kind": "x_draft", "content": {"title": "not a post"}, "claim_evidence": []}
        )


def test_domain_prompts_are_bounded_and_do_not_include_private_payloads() -> None:
    evidence: list[dict[str, object]] = [
        {"id": "e1", "summary": "Public release notes", "observed_at": "2026-07-01"}
    ]
    prompts = (
        build_recommendation_prompt(
            "Opportunity",
            "increase organic clicks",
            "seo_page_update",
            "seo_page_update",
            evidence,
            correlation_id="run:recommendation:opportunity",
        ),
        build_prepared_asset_prompt(
            "SEO recommendation", "seo_page_update", evidence, correlation_id="run:asset:1"
        ),
        build_risk_review_prompt(
            "x_draft",
            {"post": "A supported public claim"},
            evidence,
            correlation_id="run:risk:1",
        ),
    )

    assert all(prompt.messages and len(prompt.messages[-1].content) <= 12_000 for prompt in prompts)
    assert all("private_payload" not in prompt.messages[-1].content for prompt in prompts)


def test_evidence_selection_rejects_cross_tenant_stale_private_and_unselected_ids() -> None:
    workspace_id, product_id, opportunity_id = uuid4(), uuid4(), uuid4()
    valid = EvidenceCandidate(
        id=uuid4(),
        workspace_id=workspace_id,
        product_id=product_id,
        opportunity_id=opportunity_id,
        public_safe_summary="Public and current",
        freshness_status="fresh",
        observed_at=datetime.now(UTC),
    )
    assert validate_evidence_selection(
        [valid.id], [valid], workspace_id, product_id, opportunity_id
    ) == [valid]

    for changed in (
        {"workspace_id": uuid4()},
        {"product_id": uuid4()},
        {"opportunity_id": uuid4()},
        {"freshness_status": "stale"},
        {"public_safe_summary": ""},
    ):
        candidate = EvidenceCandidate(**(valid.__dict__ | changed))
        with pytest.raises(ValueError):
            validate_evidence_selection(
                [candidate.id], [candidate], workspace_id, product_id, opportunity_id
            )
    with pytest.raises(ValueError):
        validate_evidence_selection([uuid4()], [valid], workspace_id, product_id, opportunity_id)


def test_claim_coverage_secret_detection_and_output_compatibility() -> None:
    validate_asset_claims(["claim-1"], {"claim-1": ["e1"]}, {"e1"})
    with pytest.raises(ValueError):
        validate_asset_claims(["claim-1"], {}, {"e1"})
    with pytest.raises(ValueError):
        validate_asset_claims([], {}, set(), content="Authorization: Bearer secret-token")

    validate_output_compatibility("seo_page_update", "organic_clicks", "seo_page_update")
    with pytest.raises(ValueError):
        validate_output_compatibility("seo_page_update", "organic_clicks", "x_draft")


@pytest.mark.parametrize(
    ("action_family", "output_type"),
    [
        (ActionFamily.seo_snippet_update, "seo_page_update"),
        (ActionFamily.existing_page_optimization, "seo_page_update"),
        (ActionFamily.content_refresh_investigation, "blog_brief"),
        (ActionFamily.seo_page_update, "seo_page_update"),
        (ActionFamily.blog_brief, "blog_brief"),
        (ActionFamily.product_update, "product_update"),
        (ActionFamily.dedicated_content_creation, "blog_brief"),
        (ActionFamily.page_quality_fix, "seo_page_update"),
        (ActionFamily.x_draft, "x_draft"),
    ],
)
def test_every_detector_action_family_has_one_deterministic_output(
    action_family: ActionFamily, output_type: str
) -> None:
    assert output_type_for_action_family(action_family.value) == output_type


@pytest.mark.parametrize(
    ("deterministic", "ai", "expected"),
    [
        ("block", "pass", "block"),
        ("pass", "block", "block"),
        ("review", "pass", "review"),
        ("pass", "review", "review"),
        ("pass", "pass", "pass"),
    ],
)
def test_final_risk_precedence(deterministic: str, ai: str, expected: str) -> None:
    assert final_risk_outcome(deterministic, ai) == expected


def test_operator_ai_metadata_has_canonical_tenant_scoped_records() -> None:
    expected = {
        "operator_recommendations",
        "operator_recommendation_evidence",
        "prepared_assets",
        "action_risk_reviews",
    }
    assert expected <= set(Base.metadata.tables)
    for name in expected:
        table = Base.metadata.tables[name]
        assert {"workspace_id", "product_id"} <= set(table.c.keys())
        assert all(
            constraint.name
            for constraint in table.constraints
            if isinstance(
                constraint,
                (PrimaryKeyConstraint, ForeignKeyConstraint, UniqueConstraint, CheckConstraint),
            )
        )
        assert any(
            isinstance(constraint, ForeignKeyConstraint)
            and {column.name for column in constraint.columns} >= {"workspace_id", "product_id"}
            for constraint in table.constraints
        )
    assets = Base.metadata.tables["prepared_assets"]
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns)
        == ("workspace_id", "product_id", "recommendation_id", "revision")
        for constraint in assets.constraints
    )


def test_migration_0013_follows_llm_baseline_and_is_append_only_for_reviews() -> None:
    source = (
        Path(__file__).parents[1] / "alembic/versions/0013_operator_ai_outputs.py"
    ).read_text()
    assert 'revision: str = "0013_operator_ai_outputs"' in source
    assert 'down_revision: str | None = "0012_llm_executions"' in source
    assert "action_risk_reviews_append_only" in source
    assert "operator_recommendation_evidence" in source
    assert 'sa.PrimaryKeyConstraint("id", name=' in source
    assert "sa.ForeignKeyConstraint(" in source
    assert "sa.UniqueConstraint(" in source
    assert "sa.CheckConstraint(" in source
    # Every table constraint in this migration must be explicit, not naming-convention dependent.
    assert 'sa.PrimaryKeyConstraint("id")' not in source
    assert 'sa.CheckConstraint("revision > 0")' not in source


def test_phase5_approval_requires_exact_non_blocked_asset_revision() -> None:
    workspace_id, product_id, action_id, asset_id = uuid4(), uuid4(), uuid4(), uuid4()
    action = PlanAction(
        id=action_id,
        workspace_id=workspace_id,
        product_id=product_id,
        approval_level="required",
    )
    asset = PreparedAsset(
        id=asset_id,
        workspace_id=workspace_id,
        product_id=product_id,
        action_id=action_id,
        revision=1,
    )
    approval = ApprovalRequest(
        workspace_id=workspace_id,
        product_id=product_id,
        action_id=action_id,
        asset_id=asset_id,
        asset_revision=1,
        status="approved",
    )
    review = ActionRiskReview(
        workspace_id=workspace_id,
        product_id=product_id,
        asset_id=asset_id,
        asset_revision=1,
        final_outcome="pass",
    )

    assert action_has_required_approval(action, approval, asset, review)
    approval.asset_revision = 0
    assert not action_has_required_approval(action, approval, asset, review)
    approval.asset_revision = 1
    review.final_outcome = "block"
    assert not action_has_required_approval(action, approval, asset, review)
