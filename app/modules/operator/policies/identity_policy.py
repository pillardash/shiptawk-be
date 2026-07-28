import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        values = [_canonical(item) for item in value]
        return sorted(
            values, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))
        )
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (UUID, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def identity_fingerprint(
    product_id: UUID,
    opportunity_type: str,
    subject_kind: str,
    subject_keys: dict[str, str],
    action_family: str,
) -> str:
    digest = _digest(
        (
            str(product_id),
            opportunity_type,
            subject_kind,
            tuple(sorted(subject_keys.items())),
            action_family,
        )
    )
    return f"opp:v1:{digest}"


def observation_fingerprint(
    *,
    detector_id: str,
    detector_version: str,
    policy_versions: dict[str, str],
    periods: Any,
    evidence: Any,
    metrics: Any,
    profile_id: UUID | None,
    source_ids: Any,
) -> str:
    return _digest(
        {
            "detector": (detector_id, detector_version),
            "policies": policy_versions,
            "periods": periods,
            "evidence": evidence,
            "metrics": metrics,
            "profile_id": profile_id,
            "source_ids": source_ids,
        }
    )
