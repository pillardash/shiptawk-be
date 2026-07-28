from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_CONTRACT_PATH = Path("contracts/openapi.json")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

CONTRACT_ENVIRONMENT = {
    "ALLOWED_HOSTS": "*",
    "API_PREFIX": "/api/v1",
    "APP_ENV": "test",
    "APP_NAME": "Backend API",
    "APP_VERSION": "0.1.0",
    "CORS_ALLOW_CREDENTIALS": "true",
    "CORS_ORIGINS": "http://localhost:3000,http://localhost:3001",
    "DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/app_contract",
    "DOCS_ENABLED": "false",
    "FRONTEND_URL": "http://localhost:3000",
    "OPENAPI_ENABLED": "true",
    "PUBLIC_BACKEND_URL": "http://localhost:8000",
    "RATE_LIMIT_ENABLED": "false",
    "REDOC_ENABLED": "false",
}


def canonical_openapi(schema: object) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_contract(path: Path, schema: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_openapi(schema), encoding="utf-8")


def check_contract(path: Path, schema: object) -> bool:
    try:
        committed = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return committed == canonical_openapi(schema)


def build_openapi_schema() -> object:
    repository_root = str(REPOSITORY_ROOT)
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)

    from app.core.config import Settings, get_settings

    Settings.model_config["env_file"] = None
    for name, field in Settings.model_fields.items():
        os.environ.pop(name.upper(), None)
        if isinstance(field.alias, str):
            os.environ.pop(field.alias, None)
    os.environ.update(CONTRACT_ENVIRONMENT)
    get_settings.cache_clear()

    app_module = importlib.import_module("app.main")
    return app_module.app.openapi()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export or check the canonical OpenAPI contract")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CONTRACT_PATH,
        help=f"contract artifact path (default: {DEFAULT_CONTRACT_PATH})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the artifact differs; never modify it",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    schema = build_openapi_schema()
    output: Path = args.output
    if args.check:
        if check_contract(output, schema):
            return 0
        print(
            f"OpenAPI contract drift detected in {output}. "
            "Run `uv run python scripts/export_openapi.py` and commit the result.",
            file=sys.stderr,
        )
        return 1

    write_contract(output, schema)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
