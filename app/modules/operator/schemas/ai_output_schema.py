from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Recommendation(StrictOutput):
    kind: Literal["recommendation"]
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=4000)
    action_family: str = Field(min_length=1, max_length=64)
    output_type: Literal["seo_page_update", "blog_brief", "product_update", "x_draft"]
    expected_metric: str = Field(min_length=1, max_length=64)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    claims: list[str] = Field(default_factory=list, max_length=50)


class NoRecommendation(StrictOutput):
    kind: Literal["no_recommendation"]
    reason: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


RecommendationOutput = Annotated[Recommendation | NoRecommendation, Field(discriminator="kind")]


class ClaimEvidence(StrictOutput):
    claim: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class SEOPageContent(StrictOutput):
    title: str = Field(min_length=1, max_length=200)
    meta_description: str = Field(min_length=1, max_length=320)
    body_markdown: str = Field(min_length=1, max_length=30_000)


class BlogBriefContent(StrictOutput):
    working_title: str = Field(min_length=1, max_length=200)
    audience: str = Field(min_length=1, max_length=500)
    outline: list[str] = Field(min_length=1, max_length=30)


class ProductUpdateContent(StrictOutput):
    headline: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    highlights: list[str] = Field(min_length=1, max_length=30)


class XDraftContent(StrictOutput):
    post: str = Field(min_length=1, max_length=280)


class SEOPageUpdate(StrictOutput):
    kind: Literal["seo_page_update"]
    content: SEOPageContent
    claim_evidence: list[ClaimEvidence] = Field(default_factory=list, max_length=50)


class BlogBrief(StrictOutput):
    kind: Literal["blog_brief"]
    content: BlogBriefContent
    claim_evidence: list[ClaimEvidence] = Field(default_factory=list, max_length=50)


class ProductUpdate(StrictOutput):
    kind: Literal["product_update"]
    content: ProductUpdateContent
    claim_evidence: list[ClaimEvidence] = Field(default_factory=list, max_length=50)


class XDraft(StrictOutput):
    kind: Literal["x_draft"]
    content: XDraftContent
    claim_evidence: list[ClaimEvidence] = Field(default_factory=list, max_length=50)


class NoAsset(StrictOutput):
    kind: Literal["no_asset"]
    reason: str = Field(min_length=1, max_length=2000)


PreparedAssetOutput = Annotated[
    SEOPageUpdate | BlogBrief | ProductUpdate | XDraft | NoAsset, Field(discriminator="kind")
]


class RiskFinding(StrictOutput):
    code: str = Field(min_length=1, max_length=64)
    severity: Literal["review", "block"]
    message: str = Field(min_length=1, max_length=1000)


class RiskReview(StrictOutput):
    outcome: Literal["pass", "review", "block"]
    findings: list[RiskFinding] = Field(default_factory=list, max_length=50)


RiskReviewOutput = RiskReview
