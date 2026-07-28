import hashlib
import json

from app.modules.llm.providers.prompt_envelope import PromptEnvelope


def canonical_request_fingerprint(envelope: PromptEnvelope, route_name: str) -> str:
    payload = {
        "route": route_name,
        "envelope": envelope.model_dump(mode="json", exclude={"model", "correlation_id"}),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def result_fingerprint(output: object | None) -> str:
    canonical = json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
