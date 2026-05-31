# Backend Boilerplate Instructions

## Project Rules

- This is a reusable FastAPI backend boilerplate.
- Keep the codebase infrastructure-first, scalable, maintainable, and production-safe.
- Prefer small, focused changes over broad rewrites.
- Do not add business domains, auth, or product-specific behavior unless explicitly requested.
- Remove dead code and placeholder logic instead of keeping unused scaffolding.

## API Contract

- Request and response elements must use camelCase.
- Do not expose snake_case field names in API payloads.
- Python internals may use snake_case, but public API schemas must serialize and accept camelCase.
- Public request/response schemas must inherit from `app.shared.schemas.ApiSchema`.
- Manually serialized API schemas must use `model_dump(by_alias=True)`.
- Error payloads must expose `requestId`, not `request_id`.
- Tests for endpoints must assert camelCase request/response shapes.

## Architecture

- `app/api/` owns HTTP routing and API versioning.
- `app/core/` owns configuration, logging, middleware, and exception handling.
- `app/db/` owns SQLAlchemy setup and database health checks.
- `app/domains/` owns future business domains.
- `app/shared/` owns reusable application primitives.

## FastAPI Conventions

- Use `create_app()` from `app/main.py`.
- Keep `/health` as unversioned liveness.
- Keep versioned routes under `API_PREFIX`, default `/api/v1`.
- Use centralized exception handling for consistent error responses.
- Include request IDs in error responses and logs.

## Database

- Use sync SQLAlchemy, not async SQLAlchemy.
- Use PostgreSQL.
- Use Alembic for migrations.
- Do not create database engines/sessions directly in domain code; use the shared DB session utilities.

## Verification

After backend changes, run:

```bash
uv run ruff format --check .
uv run pytest
uv run ruff check .
uv run mypy app tests
```
