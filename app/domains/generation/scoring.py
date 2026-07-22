import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domains.generation.context import EffectiveGenerationContext
from app.domains.generation.summary import CommitEnrichment
from app.domains.integrations.event_normalizer import NormalizedEvent

PipelineOutcome = Literal["generate", "hold", "ignore", "block"]

_SAFETY_BLOCKLIST = [
    "secret",
    "token",
    "password",
    "credential",
    "customer data",
    "customer name",
    "customer email",
    "internal",
    "roadmap",
    "security fix",
    "cve",
    "incident",
    "hotfix",
]
_IMPACT_KEYWORDS = [
    "faster",
    "speed",
    "latency",
    "bug",
    "fix",
    "support",
    "improve",
    "launch",
    "release",
    "availability",
    "reliability",
    "billing",
    "checkout",
    "search",
    "onboarding",
    "auth",
    "security",
    "mobile",
    "dashboard",
]
_PRODUCT_RELEVANCE_KEYWORDS = [
    "product",
    "user",
    "users",
    "founder",
    "founders",
    "customer-facing",
    "onboarding",
    "workflow",
    "recap",
    "digest",
    "content",
    "post",
    "posts",
    "launch",
    "signup",
    "trial",
]
_LOW_SIGNAL_KEYWORDS = [
    "refactor",
    "cleanup",
    "cleaned up",
    "deps",
    "dependency",
    "lint",
    "format",
    "rename",
    "types",
    "test",
]
_PROOF_PATTERNS = [
    re.compile(pattern, re.IGNORECASE | re.ASCII)
    for pattern in (
        r"\bpr\b",
        r"\brelease\b",
        r"\bcommit\b",
        r"\bv\d+(?:\.\d+){0,2}\b",
        r"https?://",
        r"#[0-9]{1,8}\b",
    )
]


class ScoringModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ScoreDimensions(ScoringModel):
    user_impact: int
    novelty: int
    clarity: int
    safety: int
    proof: int


class PublicWorthinessResult(ScoringModel):
    score: int
    dimensions: ScoreDimensions
    outcome: PipelineOutcome
    rationale: str
    is_public_worthy: bool
    failed_safety_checks: list[str]


def _clamp(value: int) -> int:
    return max(0, min(100, value))


def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


class CandidateScoringEngine:
    def __init__(self, *, generate_threshold: int = 66, proof_threshold: int = 36) -> None:
        self._generate_threshold = generate_threshold
        self._proof_threshold = proof_threshold

    def score(
        self,
        *,
        event: NormalizedEvent,
        enrichment: CommitEnrichment | None,
        repo_private: bool,
        generation_context: EffectiveGenerationContext,
        drafts_created_this_week: int,
    ) -> PublicWorthinessResult:
        enrichment_text = (
            f"{enrichment.ai_summary or ''} {enrichment.ai_user_benefit or ''}"
            if enrichment is not None
            else " "
        )
        has_verified_enrichment = bool(
            enrichment
            and enrichment.ai_summary
            and enrichment.ai_summary.strip()
            and enrichment.ai_user_benefit
            and enrichment.ai_user_benefit.strip()
        )
        text = f"{event.title} {event.description} {enrichment_text}".lower()
        failed: list[str] = []
        has_product_context = bool(
            (generation_context.product_name or "").strip()
            or generation_context.messaging_angle.strip()
        )
        has_audience_context = bool(
            generation_context.product_audience.strip()
            or generation_context.account_audience.strip()
        )
        product_signals = sum(token in text for token in _PRODUCT_RELEVANCE_KEYWORDS)
        low_signals = sum(token in text for token in _LOW_SIGNAL_KEYWORDS)

        if not has_product_context and not has_audience_context:
            failed.append("missing_product_context")
        for token in _SAFETY_BLOCKLIST:
            if token in text:
                failed.append(f"safety:{token}")
        for term in generation_context.blocked_terms:
            if term.lower() in text:
                failed.append(f"blocked_term:{term}")
        for topic in generation_context.blocked_topics:
            if topic.lower() in text:
                failed.append(f"blocked_topic:{topic}")
        if event.event_type not in generation_context.preferred_event_types:
            failed.append(f"non_preferred_event_type:{event.event_type}")
        if (
            generation_context.ignored_paths
            and event.touched_paths
            and all(
                any(path.startswith(ignored) for ignored in generation_context.ignored_paths)
                for path in event.touched_paths
            )
        ):
            failed.append("ignored_paths_only")
        if drafts_created_this_week >= generation_context.max_drafts_per_week:
            failed.append("weekly_limit_reached")

        impact_matches = sum(token in text for token in _IMPACT_KEYWORDS)
        enrichment_bonus = (
            18
            if enrichment and enrichment.ai_user_benefit and enrichment.ai_user_benefit.strip()
            else 0
        )
        user_impact = _clamp(
            30
            + impact_matches * 7
            + enrichment_bonus
            + (8 if has_product_context else 0)
            + (6 if has_audience_context else 0)
            + min(24, product_signals * 4)
            - min(20, low_signals * 8)
        )
        if event.event_type == "release":
            novelty = 86
        elif event.event_type == "pull_request_merged":
            novelty = 72
        else:
            novelty = _clamp(60 + min(16, product_signals * 3) - min(14, low_signals * 5))
        clarity = _clamp(
            40
            + min(25, _utf16_length(event.title.strip()) // 3)
            + min(20, _utf16_length(event.description.strip()) // 12)
        )
        safety = _clamp(100 - len(failed) * 28 - (10 if repo_private else 0))
        if repo_private and generation_context.private_repo_mode == "conservative":
            safety = _clamp(safety - 20)
            failed.append("private_repo_conservative_mode")

        proof_text = f"{event.title} {event.description}"
        proof_signals = (
            sum(pattern.search(proof_text) is not None for pattern in _PROOF_PATTERNS)
            + (1 if event.url.startswith("http") else 0)
            + (1 if event.sha else 0)
        )
        proof = _clamp(28 + proof_signals * 14)
        if (
            event.event_type == "push"
            and has_product_context
            and product_signals < 2
            and low_signals > 0
            and not has_verified_enrichment
        ):
            failed.append("low_product_story_signal")

        dimensions = ScoreDimensions(
            user_impact=user_impact,
            novelty=novelty,
            clarity=clarity,
            safety=safety,
            proof=proof,
        )
        total = _clamp(
            _js_round(
                user_impact * 0.32 + novelty * 0.18 + clarity * 0.18 + safety * 0.2 + proof * 0.12
            )
        )
        outcome = self._outcome(total, dimensions, failed)
        return PublicWorthinessResult(
            score=total,
            dimensions=dimensions,
            outcome=outcome,
            rationale=(
                f"Outcome={outcome}; dimensions ui={user_impact}, novelty={novelty}, "
                f"clarity={clarity}, safety={safety}, proof={proof}"
            ),
            is_public_worthy=outcome == "generate",
            failed_safety_checks=failed,
        )

    def _outcome(
        self, score: int, dimensions: ScoreDimensions, failed: list[str]
    ) -> PipelineOutcome:
        hard_block = any(
            check.startswith(("safety:", "blocked_term:", "blocked_topic:")) for check in failed
        )
        hold_only = any(
            check
            in {
                "weekly_limit_reached",
                "ignored_paths_only",
                "missing_product_context",
                "low_product_story_signal",
                "private_repo_conservative_mode",
            }
            or check.startswith("non_preferred_event_type:")
            for check in failed
        )
        if hard_block or dimensions.safety < 40:
            return "block"
        if hold_only:
            return "hold"
        if score >= self._generate_threshold and dimensions.proof >= self._proof_threshold:
            return "generate"
        if score >= 46:
            return "hold"
        return "ignore"
