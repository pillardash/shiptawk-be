import math
import re

from app.modules.drafts.detectors.repetition_detector import detect_repetition
from app.modules.drafts.schemas.evidence import GeneratedVariant, PublicSafeSummary
from app.modules.drafts.schemas.memory import ContentMemory
from app.modules.drafts.schemas.ranking import RankingDimensions, TweetOption
from app.modules.integrations.events import NormalizedEvent


def _clamp(value: float) -> float:
    return max(0, min(100, value))


def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def _utf16_slice(value: str, limit: int) -> str:
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    return encoded[: limit * 2].decode("utf-16-le", errors="surrogatepass")


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _tokens(value: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9\s]", " ", value.lower())
    return {token for token in re.split(r"\s+", normalized) if token}


def _overlap(source: set[str], target: set[str]) -> float:
    if not source or not target:
        return 0.0
    return len(source & target) / min(len(source), len(target))


def _value_boost(variant: GeneratedVariant, event: NormalizedEvent, content: str) -> int:
    boost = 0
    if variant.variant == "customer_value":
        boost += 8
    elif variant.variant == "problem_solution":
        boost += 5
    elif variant.variant == "soft_launch_cta":
        boost += 2
    if variant.angle == "user_benefit":
        boost += 8
    elif variant.angle == "problem_solved":
        boost += 4
    elif variant.angle == "shipping_update" and event.event_type != "release":
        boost -= 6
    if re.search(r"\b(smoother|reliable|friction|workflow)\b", content, re.IGNORECASE):
        boost -= 4
    if re.search(
        r"\b(before|after|without|so you|so teams|so schools|helps you|helps teams|can now)\b",
        content,
        re.IGNORECASE,
    ):
        boost += 4
    return boost


class TweetOptionRanker:
    def rank(
        self,
        *,
        variants: list[GeneratedVariant],
        event: NormalizedEvent,
        summary: PublicSafeSummary,
        content_memory: ContentMemory | None = None,
    ) -> list[TweetOption]:
        claims = {claim.id: claim for claim in summary.supported_claims or []}
        provisional: list[TweetOption] = []
        for variant in variants:
            content = _utf16_slice(variant.content.strip(), 280)
            cited = " ".join(
                claims[evidence_id].text if evidence_id in claims else ""
                for evidence_id in variant.evidence_ids or []
            )
            evidence_text = cited or f"{summary.public_summary} {summary.user_benefit}"
            coverage = _overlap(_tokens(content), _tokens(evidence_text))
            repetition = detect_repetition(
                candidate=content, angle=variant.angle, memory=content_memory
            )
            novelty_base = {
                "founder_insight": 74,
                "problem_solution": 70,
                "customer_value": 68,
            }.get(variant.variant, 62)
            evidence_ids = variant.evidence_ids or []
            dimensions = RankingDimensions(
                user_impact=_clamp(45 + _js_round(coverage * 40)),
                novelty=_clamp(novelty_base - repetition.penalty),
                clarity=_clamp(80 - max(0, _utf16_length(content) - 180) / 2),
                safety=_clamp(55 if summary.safety_flags else 92),
                proof=_clamp(
                    40
                    + min(30, len(evidence_ids) * 10)
                    + (10 if any(value.startswith("enrichment.") for value in evidence_ids) else 0)
                ),
            )
            score = _clamp(
                _js_round(
                    dimensions.user_impact * 0.4
                    + dimensions.novelty * 0.1
                    + dimensions.clarity * 0.2
                    + dimensions.safety * 0.17
                    + dimensions.proof * 0.15
                )
                + _value_boost(variant, event, content)
                - _js_round(repetition.penalty * 0.55)
            )
            rationale = variant.rationale
            if repetition.reasons:
                rationale += (
                    f"; repetition penalty {repetition.penalty}: {', '.join(repetition.reasons)}"
                )
            provisional.append(
                TweetOption(
                    content=content,
                    rank=0,
                    score=score,
                    rationale=rationale,
                    angle=variant.angle,
                    variant=variant.variant,
                    evidence_ids=variant.evidence_ids,
                    dimension_scores=dimensions,
                )
            )
        provisional.sort(key=lambda option: option.score, reverse=True)
        return [
            option.model_copy(update={"rank": index}) for index, option in enumerate(provisional, 1)
        ]
