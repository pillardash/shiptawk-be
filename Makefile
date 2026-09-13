.PHONY: install dev test test-cov test-evaluations lint format format-check typecheck check contract-check migration-check migrate revision

install:
	uv sync --frozen --all-groups

dev:
	uv run uvicorn app.main:app --reload

test:
	uv run pytest

test-cov:
	uv run pytest --cov

test-evaluations:
	uv run pytest -m evaluation

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check . --fix

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy app scripts tests

contract-check:
	uv run python scripts/export_openapi.py --check

migration-check:
	uv run python scripts/check_migrations.py

check: format-check lint typecheck contract-check test-cov

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(message)"
