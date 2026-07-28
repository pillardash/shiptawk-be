import re

from app.modules.drafts.enums.generation_enum import AngleType
from app.modules.drafts.schemas.angles import SelectedAngles
from app.modules.drafts.schemas.evidence import PublicSafeSummary
from app.modules.integrations.events import NormalizedEvent

_ANGLES: tuple[AngleType, ...] = (
    "user_benefit",
    "problem_solved",
    "shipping_update",
    "technical_credibility",
    "founder_build_in_public",
    "milestone_or_traction",
)
_PROBLEM = re.compile(r"fix|bug|issue|error|lost|manual|stuck|slow|risk")
_FOUNDER = re.compile(r"build|ship|progress|iteration")
_MILESTONE = re.compile(r"release|launch|v\d|milestone", re.ASCII)


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


class AngleSelector:
    def select(self, event: NormalizedEvent, summary: PublicSafeSummary) -> SelectedAngles:
        text = f"{summary.public_summary} {summary.user_benefit}".lower()
        ranked: list[tuple[int, int, AngleType]] = []
        for ordinal, angle in enumerate(_ANGLES):
            score = 22 if angle == "user_benefit" else 10
            if angle == "shipping_update":
                score += 18 if event.event_type == "release" else 2
            if angle == "technical_credibility":
                score += 14 if event.event_type == "pull_request_merged" else 6
            if angle == "user_benefit" and _utf16_length(summary.user_benefit) > 20:
                score += 34
            if angle == "problem_solved" and _PROBLEM.search(text):
                score += 24
            if angle == "founder_build_in_public" and _FOUNDER.search(text):
                score += 16
            if angle == "milestone_or_traction" and _MILESTONE.search(text):
                score += 12
            ranked.append((score, ordinal, angle))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return SelectedAngles(
            best=ranked[0][2] if ranked else "shipping_update",
            alternates=[item[2] for item in ranked[1:4]],
        )
