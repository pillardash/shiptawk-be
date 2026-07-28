import json
from pathlib import Path

from scripts.export_openapi import canonical_openapi, check_contract, write_contract


def test_canonical_openapi_is_sorted_and_has_one_trailing_newline() -> None:
    schema = {"paths": {"/z": {}, "/a": {}}, "openapi": "3.1.0"}

    rendered = canonical_openapi(schema)

    assert rendered == (
        '{\n  "openapi": "3.1.0",\n  "paths": {\n    "/a": {},\n    "/z": {}\n  }\n}\n'
    )


def test_check_contract_accepts_canonical_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "openapi.json"
    schema = {"openapi": "3.1.0", "paths": {}}
    write_contract(artifact, schema)

    assert check_contract(artifact, schema) is True


def test_check_contract_reports_drift_without_modifying_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "openapi.json"
    original = json.dumps({"openapi": "3.0.0"}) + "\n"
    artifact.write_text(original, encoding="utf-8")

    matches = check_contract(artifact, {"openapi": "3.1.0"})

    assert matches is False
    assert artifact.read_text(encoding="utf-8") == original


def test_check_contract_reports_missing_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "openapi.json"

    assert check_contract(artifact, {"openapi": "3.1.0"}) is False
