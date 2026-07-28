import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class EvidenceCandidate:
    id: UUID
    workspace_id: UUID
    product_id: UUID
    opportunity_id: UUID | None
    public_safe_summary: str
    freshness_status: str
    observed_at: datetime


def validate_evidence_selection(
    selected_ids: list[UUID],
    candidates: list[EvidenceCandidate],
    workspace_id: UUID,
    product_id: UUID,
    opportunity_id: UUID,
) -> list[EvidenceCandidate]:
    by_id = {item.id: item for item in candidates}
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("duplicate_evidence_id")
    selected: list[EvidenceCandidate] = []
    for evidence_id in selected_ids:
        item = by_id.get(evidence_id)
        if item is None:
            raise ValueError("unknown_evidence_id")
        if (
            item.workspace_id != workspace_id
            or item.product_id != product_id
            or item.opportunity_id != opportunity_id
        ):
            raise ValueError("cross_tenant_or_opportunity_evidence")
        if item.freshness_status != "fresh" or not item.public_safe_summary.strip():
            raise ValueError("evidence_not_public_safe_and_fresh")
        selected.append(item)
    return selected


_SECRET = re.compile(
    r"(?i)(authorization\s*:\s*bearer|api[_-]?key|password|private[_-]?key|secret[_-]?token)"
)
_RESTRICTED_LANGUAGE = re.compile(
    r"(?i)\b(guaranteed?|risk[- ]free|no risk|best on the market|100% certain)\b"
)
_INSTRUCTION_FOLLOWING = re.compile(
    r"(?i)(ignore (?:all |the )?(?:previous|system) instructions|reveal (?:the )?system prompt|"
    r"developer message|jailbreak|do not follow the system)"
)


def validate_asset_claims(
    claims: list[str],
    claim_evidence: dict[str, list[str]],
    allowed_evidence_ids: set[str],
    *,
    content: object = "",
) -> None:
    serialized = json.dumps(content, sort_keys=True) if not isinstance(content, str) else content
    if _SECRET.search(serialized):
        raise ValueError("secret_or_private_content")
    if _RESTRICTED_LANGUAGE.search(serialized):
        raise ValueError("restricted_language")
    if _INSTRUCTION_FOLLOWING.search(serialized):
        raise ValueError("prompt_injection_instruction_following")
    for claim in claims:
        cited = claim_evidence.get(claim, [])
        if not cited or not set(cited) <= allowed_evidence_ids:
            raise ValueError("asset_claim_without_authoritative_evidence")


_OUTPUT_BY_ACTION_FAMILY = {
    "seo_snippet_update": "seo_page_update",
    "existing_page_optimization": "seo_page_update",
    "content_refresh_investigation": "blog_brief",
    "seo_page_update": "seo_page_update",
    "blog_brief": "blog_brief",
    "product_update": "product_update",
    "dedicated_content_creation": "blog_brief",
    "page_quality_fix": "seo_page_update",
    "x_draft": "x_draft",
}


def output_type_for_action_family(action_family: str) -> str:
    try:
        return _OUTPUT_BY_ACTION_FAMILY[action_family]
    except KeyError as exc:
        raise ValueError("unsupported_action_family") from exc


def validate_output_compatibility(
    action_family: str, expected_metric: str, output_type: str
) -> None:
    if not expected_metric.strip() or output_type_for_action_family(action_family) != output_type:
        raise ValueError("incompatible_action_metric_or_output")
