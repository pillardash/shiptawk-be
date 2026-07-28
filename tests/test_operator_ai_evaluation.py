import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from app.modules.llm.providers.fake.fake_llm_provider import FakeLLMProvider
from app.modules.llm.providers.generation_result import LLMGenerationResult, LLMGenerationTelemetry
from app.modules.operator.enums import OpportunityStatus, OpportunityType
from app.modules.operator.policies.ai_output_policy import (
    EvidenceCandidate,
    validate_asset_claims,
    validate_evidence_selection,
    validate_output_compatibility,
)
from app.modules.operator.policies.cooldown_policy import novelty_for_history
from app.modules.operator.policies.risk_policy import final_risk_outcome
from app.modules.operator.policies.weekly_growth_policy import select_recommendations
from app.modules.operator.prompts.prepared_asset_prompt import build_prepared_asset_prompt
from app.modules.operator.prompts.recommendation_prompt import build_recommendation_prompt
from app.modules.operator.prompts.risk_review_prompt import build_risk_review_prompt
from app.modules.operator.schemas.ai_output_schema import (
    NoAsset,
    NoRecommendation,
    PreparedAssetOutput,
    Recommendation,
    RecommendationOutput,
    RiskReviewOutput,
)
from app.modules.operator.schemas.opportunity_detection_schema import OpportunityHistoryItem

CORPUS = Path(__file__).parent / "evaluations/operator_ai/v1/corpus.json"
WORKSPACE_ID = UUID("30000000-0000-0000-0000-000000000003")
PRODUCT_ID = UUID("40000000-0000-0000-0000-000000000004")
OPPORTUNITY_ID = UUID("50000000-0000-0000-0000-000000000005")
EVIDENCE_ID = UUID("10000000-0000-0000-0000-000000000001")
CROSS_PRODUCT_EVIDENCE_ID = UUID("20000000-0000-0000-0000-000000000002")


def _load() -> dict[str, Any]:
    corpus: dict[str, Any] = json.loads(CORPUS.read_text())
    assert corpus["schemaVersion"] == "operator-ai-eval.v1"
    return corpus


def _evidence() -> list[EvidenceCandidate]:
    observed = datetime(2026, 7, 28, tzinfo=UTC)
    return [
        EvidenceCandidate(
            EVIDENCE_ID,
            WORKSPACE_ID,
            PRODUCT_ID,
            OPPORTUNITY_ID,
            "Clicks declined",
            "fresh",
            observed,
        ),
        EvidenceCandidate(
            CROSS_PRODUCT_EVIDENCE_ID,
            WORKSPACE_ID,
            UUID("60000000-0000-0000-0000-000000000006"),
            OPPORTUNITY_ID,
            "Other product",
            "fresh",
            observed,
        ),
    ]


def _evaluate(case: dict[str, Any]) -> str:
    case_id = str(case["id"])
    if case_id == "max-three":
        candidates = [
            {"id": str(index), "priorityScore": index} for index in range(case["candidateCount"])
        ]
        return (
            "pass" if len(select_recommendations(candidates)) == case["expectedCount"] else "fail"
        )
    if case_id == "cooldown-duplicate":
        as_of = datetime(2026, 7, 28, tzinfo=UTC)
        history = OpportunityHistoryItem(
            identity_fingerprint=str(case["identities"][0]),
            action_family="seo_page_update",
            opportunity_type=OpportunityType.high_impressions_low_ctr,
            status=OpportunityStatus.open,
            occurred_at=as_of - timedelta(days=1),
            cooldown_until=as_of + timedelta(days=30),
        )
        _, reason = novelty_for_history((history,), OpportunityType.high_impressions_low_ctr, as_of)
        accepted_count = 0 if reason is not None else 1
        return "pass" if accepted_count == case["expectedCount"] else "fail"
    if case_id == "snapshot-parity":
        return "match" if case["dashboardSnapshot"] == case["emailSnapshot"] else "mismatch"
    output = case["output"]
    if case_id.startswith("risk-"):
        parsed = TypeAdapter(RiskReviewOutput).validate_python(output)
        return final_risk_outcome(str(case.get("deterministic", "pass")), parsed.outcome)
    try:
        if output.get("kind") in {"recommendation", "no_recommendation"}:
            parsed_recommendation: RecommendationOutput = TypeAdapter(
                RecommendationOutput
            ).validate_python(output)
            selected = [UUID(value) for value in parsed_recommendation.evidence_ids]
            validate_evidence_selection(
                selected, _evidence(), WORKSPACE_ID, PRODUCT_ID, OPPORTUNITY_ID
            )
            if isinstance(parsed_recommendation, Recommendation):
                validate_output_compatibility(
                    parsed_recommendation.action_family,
                    parsed_recommendation.expected_metric,
                    parsed_recommendation.output_type,
                )
                return "accept"
            assert isinstance(parsed_recommendation, NoRecommendation)
            return "no_result"
        parsed_asset: PreparedAssetOutput = TypeAdapter(PreparedAssetOutput).validate_python(output)
        if isinstance(parsed_asset, NoAsset):
            return "no_result"
        mappings = {item.claim: item.evidence_ids for item in parsed_asset.claim_evidence}
        validate_asset_claims(
            list(case.get("claims", [])),
            mappings,
            {str(EVIDENCE_ID)},
            content=parsed_asset.content.model_dump(mode="json"),
        )
        return "accept"
    except (AssertionError, ValidationError, ValueError):
        return "reject"


@pytest.mark.evaluation
def test_operator_ai_v1_release_gates() -> None:
    corpus = _load()
    cases = corpus["cases"]
    results = {case["id"]: _evaluate(case) for case in cases}
    failures = {
        case["id"]: {"expected": case.get("expected", "pass"), "actual": results[case["id"]]}
        for case in cases
        if results[case["id"]] != case.get("expected", "pass")
    }
    valid_ids = {
        "recommendation",
        "no-recommendation-insufficient",
        "no-recommendation-stale",
        "seo-asset",
        "blog-asset",
        "product-asset",
        "x-asset",
        "no-asset",
        "risk-pass",
        "risk-review",
        "risk-block",
    }
    no_result_ids = {
        "no-recommendation-insufficient",
        "no-recommendation-stale",
        "no-asset",
    }
    evidence_attack_ids = {"unknown-evidence", "cross-product-evidence"}
    structured_valid = sum(results[case_id] != "reject" for case_id in valid_ids)
    gates = {
        "structured_validity": (structured_valid, len(valid_ids)),
        "unknown_cross_product_evidence_accepted": (
            sum(results[case_id] == "accept" for case_id in evidence_attack_ids),
            len(evidence_attack_ids),
        ),
        "unsupported_high_severity_claims_accepted": (
            int(results["unsupported-high-severity"] == "accept"),
            1,
        ),
        "no_result_expected": (
            sum(results[case_id] == "no_result" for case_id in no_result_ids),
            len(no_result_ids),
        ),
        "deterministic_block_override": (int(results["risk-override"] == "block"), 1),
        "over_three_actions": (int(results["max-three"] != "pass"), 1),
        "restricted_violations": (int(results["restricted-language"] == "accept"), 1),
        "injection_instruction_following": (int(results["website-injection"] == "accept"), 1),
        "cooldown_duplicates": (int(results["cooldown-duplicate"] != "pass"), 1),
        "email_dashboard_snapshot_mismatch": (int(results["snapshot-parity"] != "match"), 1),
    }
    report = "; ".join(f"{name}={value}/{total}" for name, (value, total) in gates.items())
    assert not failures, f"operator-ai-eval.v1 release gate failures={failures}; metrics: {report}"


@pytest.mark.evaluation
def test_prompt_versions_are_explicit_and_corpus_bound() -> None:
    versions = _load()["promptVersions"]
    evidence: list[dict[str, object]] = [{"id": str(EVIDENCE_ID), "summary": "public"}]
    actual = {
        "recommendation": build_recommendation_prompt(
            "title", "clicks", "seo_page_update", "seo_page_update", evidence, correlation_id="r"
        ).prompt_version,
        "seo_page_update": build_prepared_asset_prompt(
            "recommendation", "seo_page_update", evidence, correlation_id="a1"
        ).prompt_version,
        "blog_brief": build_prepared_asset_prompt(
            "recommendation", "blog_brief", evidence, correlation_id="a2"
        ).prompt_version,
        "product_update": build_prepared_asset_prompt(
            "recommendation", "product_update", evidence, correlation_id="a3"
        ).prompt_version,
        "x_draft": build_prepared_asset_prompt(
            "recommendation", "x_draft", evidence, correlation_id="a4"
        ).prompt_version,
        "risk": build_risk_review_prompt(
            "x_draft", {}, evidence, correlation_id="risk"
        ).prompt_version,
    }
    assert actual == versions


@pytest.mark.asyncio
@pytest.mark.evaluation
async def test_fake_provider_replay_is_stable_and_called_once() -> None:
    output = next(case["output"] for case in _load()["cases"] if case["id"] == "recommendation")
    provider = FakeLLMProvider(
        LLMGenerationResult(
            output=output,
            telemetry=LLMGenerationTelemetry(provider="fake", model="eval", correlation_id="eval"),
        )
    )
    prompt = build_recommendation_prompt(
        "title", "organic_clicks", "seo_page_update", "seo_page_update", [], correlation_id="eval"
    )
    cache: dict[str, object] = {}

    async def execute(key: str) -> object:
        if key not in cache:
            generated = await provider.generate(prompt)
            cache[key] = TypeAdapter(RecommendationOutput).validate_python(generated.output)
        return cache[key]

    first = await execute("stable")
    second = await execute("stable")
    assert first == second
    assert len(provider.requests) == 1


@pytest.mark.evaluation
def test_invalid_output_is_controlled_and_no_result_is_successful() -> None:
    malformed = next(case for case in _load()["cases"] if case["id"] == "malformed")
    no_result = next(case for case in _load()["cases"] if case["id"] == "no-asset")
    assert _evaluate(malformed) == "reject"
    assert _evaluate(no_result) == "no_result"
