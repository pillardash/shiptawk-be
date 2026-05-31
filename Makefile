.PHONY: install dev test test-cov lint format format-check typecheck check migrate revision

install:
	uv sync

dev:
	uv run fastapi dev app/main.py

test:
	uv run pytest

test-cov:
	uv run pytest --cov

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check . --fix

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy app tests

check: format-check lint typecheck test

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(message)"
