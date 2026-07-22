import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AngleType = Literal[
    "user_benefit",
    "problem_solved",
    "shipping_update",
    "technical_credibility",
    "founder_build_in_public",
    "milestone_or_traction",
    "weekly_roundup",
]
_ANGLE_VALUES = {
    "user_benefit",
    "problem_solved",
    "shipping_update",
    "technical_credibility",
    "founder_build_in_public",
    "milestone_or_traction",
    "weekly_roundup",
}
_WEAK_ANGLES = {"shipping_update", "weekly_roundup"}
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "this",
    "to",
    "we",
    "with",
    "you",
}


class MemoryModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class ContentMemory(MemoryModel):
    recent_approved_content: list[str] = Field(alias="recentApprovedContent")
    approved_angles: list[AngleType] = Field(alias="approvedAngles")


class RepetitionResult(MemoryModel):
    max_similarity: float
    repeated_angle_count: int
    penalty: int
    reasons: list[str]


class DraftMemoryInput(MemoryModel):
    content: str
    source_event_payload: dict[str, object]


def normalize_content(value: str) -> str:
    output = value.lower()
    output = re.sub(r"https?://\S+", " ", output)
    output = re.sub(r"[^a-z0-9\s]", " ", output)
    return re.sub(r"\s+", " ", output).strip()


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_content(value).split(" ")
        if len(token) > 2 and token not in _STOP_WORDS
    }


def token_overlap(source: str, target: str) -> float:
    source_tokens = _tokens(source)
    target_tokens = _tokens(target)
    if not source_tokens or not target_tokens:
        return 0.0
    return len(source_tokens & target_tokens) / min(len(source_tokens), len(target_tokens))


def similarity_score(source: str, target: str) -> float:
    normalized_source = normalize_content(source)
    normalized_target = normalize_content(target)
    if not normalized_source or not normalized_target:
        return 0.0
    if normalized_source == normalized_target:
        return 1.0
    return token_overlap(normalized_source, normalized_target)


def build_content_memory(drafts: list[DraftMemoryInput]) -> ContentMemory:
    content: list[str] = []
    angles: list[AngleType] = []
    for draft in drafts:
        content.append(draft.content)
        angle = draft.source_event_payload.get("selected_angle")
        if isinstance(angle, str) and angle in _ANGLE_VALUES:
            angles.append(angle)  # type: ignore[arg-type]
    return ContentMemory(recent_approved_content=content, approved_angles=angles)


def detect_repetition(
    *, candidate: str, angle: str, memory: ContentMemory | None = None
) -> RepetitionResult:
    recent = memory.recent_approved_content if memory is not None else []
    max_similarity = max(
        (similarity_score(candidate, previous) for previous in recent), default=0.0
    )
    approved_angles = memory.approved_angles if memory is not None else []
    repeated_angle_count = sum(previous == angle for previous in approved_angles)
    reasons: list[str] = []
    penalty = 0
    if max_similarity >= 0.9:
        penalty += 32
        reasons.append("near_duplicate_recent_draft")
    elif max_similarity >= 0.68:
        penalty += 18
        reasons.append("repeated_claim")
    elif max_similarity >= 0.52:
        penalty += 8
        reasons.append("similar_recent_draft")
    if repeated_angle_count > 0:
        per_angle = 8 if angle in _WEAK_ANGLES else 4
        penalty += min(16, repeated_angle_count * per_angle)
        reasons.append(f"repeated_angle:{angle}")
    return RepetitionResult(
        max_similarity=max_similarity,
        repeated_angle_count=repeated_angle_count,
        penalty=min(48, penalty),
        reasons=reasons,
    )
