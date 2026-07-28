from uuid import uuid4

from app.modules.operator.policies.identity_policy import (
    identity_fingerprint,
    observation_fingerprint,
)


def test_identity_is_prefixed_and_subject_key_order_independent() -> None:
    product_id = uuid4()
    first = identity_fingerprint(product_id, "type", "query", {"a": "1", "b": "2"}, "action")
    second = identity_fingerprint(product_id, "type", "query", {"b": "2", "a": "1"}, "action")
    assert first == second
    assert first.startswith("opp:v1:") and len(first) == 71


def test_observation_is_stable_for_semantic_set_permutations() -> None:
    first = observation_fingerprint(
        detector_id="d",
        detector_version="1",
        policy_versions={"b": "2", "a": "1"},
        periods={"dates": ["b", "a"]},
        evidence=[{"key": "b"}, {"key": "a"}],
        metrics={"queries": [{"id": 2}, {"id": 1}]},
        profile_id=None,
        source_ids=["z", "a"],
    )
    second = observation_fingerprint(
        detector_id="d",
        detector_version="1",
        policy_versions={"a": "1", "b": "2"},
        periods={"dates": ["a", "b"]},
        evidence=[{"key": "a"}, {"key": "b"}],
        metrics={"queries": [{"id": 1}, {"id": 2}]},
        profile_id=None,
        source_ids=["a", "z"],
    )
    assert first == second
