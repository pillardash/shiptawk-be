from app.modules.operator.enums import EvaluationOutcome, EvaluationReason, OpportunityType
from app.modules.operator.schemas.opportunity_detection_schema import DetectorEvaluation

PRECEDENCE = {
    OpportunityType.high_impressions_low_ctr: 0,
    OpportunityType.existing_page_needs_improvement: 1,
    OpportunityType.ranking_within_reach: 2,
    OpportunityType.search_decline: 3,
    OpportunityType.shipped_but_not_marketed: 4,
    OpportunityType.search_demand_without_dedicated_content: 5,
}


def rank_and_collapse(
    evaluations: tuple[DetectorEvaluation, ...],
) -> tuple[DetectorEvaluation, ...]:
    ordered = sorted(
        evaluations,
        key=lambda e: (
            -(e.candidate.scores.priority if e.candidate else -1),
            e.candidate.opportunity_type.value if e.candidate else "~",
            e.candidate.identity_fingerprint if e.candidate else "",
            e.candidate.observation_fingerprint if e.candidate else "",
        ),
    )
    owners: dict[tuple[str, tuple[tuple[str, str], ...]], DetectorEvaluation] = {}
    result: list[DetectorEvaluation] = []
    for item in ordered:
        if item.outcome != EvaluationOutcome.accepted or item.candidate is None:
            result.append(item)
            continue
        group = (item.candidate.subject_kind, tuple(sorted(item.candidate.subject_keys.items())))
        current = owners.get(group)
        if current is None:
            owners[group] = item
            result.append(item)
            continue
        winner = min(
            (current, item),
            key=lambda e: (
                PRECEDENCE[e.candidate.opportunity_type] if e.candidate is not None else 999
            ),
        )
        loser = item if winner is current else current
        superseded = loser.model_copy(
            update={
                "outcome": EvaluationOutcome.superseded_in_run,
                "reason_codes": (EvaluationReason.lower_priority,),
            }
        )
        if winner is item:
            result[result.index(current)] = superseded
            owners[group] = item
            result.append(item)
        else:
            result.append(superseded)
    return tuple(
        sorted(
            result,
            key=lambda e: (
                -(e.candidate.scores.priority if e.candidate else -1),
                e.candidate.opportunity_type.value if e.candidate else "~",
                e.candidate.identity_fingerprint if e.candidate else "",
                e.candidate.observation_fingerprint if e.candidate else "",
            ),
        )
    )
