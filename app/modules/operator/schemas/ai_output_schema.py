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


class SeoPageUpdateContent(StrictOutput):
    target_page: str = Field(min_length=1, max_length=2000)
    target_query_intent: str = Field(min_length=1, max_length=1000)
    proposed_title: str = Field(min_length=1, max_length=200)
    proposed_meta_description: str = Field(min_length=1, max_length=320)
    revised_h1_or_hero: str = Field(min_length=1, max_length=2000)
    missing_sections: list[str] = Field(default_factory=list, max_length=30)
    internal_links: list[str] = Field(default_factory=list, max_length=50)
    conversion_action: str = Field(min_length=1, max_length=1000)


class BlogBriefContent(StrictOutput):
    target_query: str = Field(min_length=1, max_length=500)
    search_intent: str = Field(min_length=1, max_length=500)
    audience: str = Field(min_length=1, max_length=500)
    angle: str = Field(min_length=1, max_length=1000)
    recommended_title: str = Field(min_length=1, max_length=200)
    outline: list[str] = Field(min_length=1, max_length=30)
    questions_to_answer: list[str] = Field(default_factory=list, max_length=30)
    internal_links: list[str] = Field(default_factory=list, max_length=50)
    product_cta: str = Field(min_length=1, max_length=1000)


class ProductUpdateContent(StrictOutput):
    short_announcement: str = Field(min_length=1, max_length=1000)
    email_update: str = Field(min_length=1, max_length=8000)
    changelog_entry: str = Field(min_length=1, max_length=4000)
    customer_benefit: str = Field(min_length=1, max_length=2000)
    shipping_evidence: list[str] = Field(min_length=1, max_length=30)


class XDraftContent(StrictOutput):
    post: str = Field(min_length=1, max_length=280)


class SEOPageUpdate(StrictOutput):
    kind: Literal["seo_page_update"]
    content: SeoPageUpdateContent
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
