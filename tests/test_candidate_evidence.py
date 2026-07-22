from app.domains.generation.evidence import (
    CandidateEvidenceValidator,
    EvidenceValidationResult,
    GeneratedVariant,
    PublicSafeSummary,
    SupportedClaim,
)


def summary() -> PublicSafeSummary:
    return PublicSafeSummary(
        internal_summary="Release",
        public_summary="Onboarding is faster",
        user_benefit="",
        target_audience="founders",
        safety_flags=[],
        confidence=90,
        supported_claims=[
            SupportedClaim(
                id="event.change.1",
                source="event",
                kind="change",
                text="Improved onboarding",
            ),
            SupportedClaim(
                id="product.proof.1",
                source="product_context",
                kind="proof_point",
                text="40% faster setup",
            ),
        ],
    )


def variant(**overrides: object) -> GeneratedVariant:
    values: dict[str, object] = {
        "content": "Improved onboarding is now available in Shiptawk.",
        "variant": "customer_value",
        "angle": "user_benefit",
        "rationale": "Supported release",
        "evidence_ids": ["event.change.1"],
    }
    values.update(overrides)
    return GeneratedVariant.model_validate(values)


def validate(candidate: GeneratedVariant) -> EvidenceValidationResult:
    return CandidateEvidenceValidator().validate(
        variants=[candidate],
        summary=summary(),
        product_name="Shiptawk",
        subject_name="Shiptawk",
        repo_name="acme/web",
    )


def test_accepts_candidate_grounded_in_known_evidence() -> None:
    result = validate(variant())

    assert result.accepted == [variant()]
    assert result.rejected == []


def test_rejects_unknown_and_unrelated_evidence() -> None:
    unknown = validate(variant(evidence_ids=["invented.1"]))
    unrelated = validate(variant(content="Billing dashboards forecast expansion revenue."))

    assert unknown.rejected[0].reasons == ["unknown_evidence"]
    assert unrelated.rejected[0].reasons == ["unsupported_claim"]


def test_metrics_must_appear_exactly_in_cited_claims() -> None:
    unsupported = validate(variant(content="Onboarding is 40% faster."))
    supported = validate(
        variant(content="Onboarding is 40% faster.", evidence_ids=["product.proof.1"])
    )

    assert unsupported.rejected[0].reasons == ["unsupported_metric"]
    assert supported.accepted != []


def test_collects_reasons_in_legacy_rule_order() -> None:
    result = validate(
        variant(
            content="Web completely guarantees onboarding for 100% of teams.",
            evidence_ids=[],
        )
    )

    assert result.rejected[0].reasons == [
        "missing_evidence",
        "unsupported_metric",
        "unsupported_absolute",
        "wrong_product",
    ]


def test_no_claims_disable_evidence_and_overlap_requirements() -> None:
    empty_summary = summary().model_copy(update={"supported_claims": []})

    result = CandidateEvidenceValidator().validate(
        variants=[variant(evidence_ids=[])],
        summary=empty_summary,
        product_name=None,
        subject_name="web",
        repo_name="acme/web",
    )

    assert result.accepted != []
