# FastAPI Boilerplate Engineering Guide

## Purpose

- This repository is a reusable, production-oriented FastAPI backend foundation.
- Keep changes infrastructure-first, scalable, maintainable, secure, and useful outside any one product.
- Auth and user domains are existing reusable capabilities. Do not add product-specific domains or behavior.
- Prefer small, focused changes and remove dead or placeholder code instead of preserving speculative scaffolding.

## Reusability Standard

- Generic configuration, middleware, observability, database, auth, cache, job, email, storage, and provider-neutral AI primitives may belong here.
- Product tables, vocabulary, prompts, workflows, provider choices, policy thresholds, and UI assumptions do not belong here.
- Do not add a feature solely because one downstream product might need it.
- An upstream contribution must be independently configurable, tested without product fixtures, documented, and free of product assumptions.

## API Contract

- Public request and response fields use camelCase; Python internals use snake_case.
- Public schemas inherit from `app.shared.schemas.ApiSchema`.
- Manually serialized public schemas use `model_dump(by_alias=True)`.
- Error payloads expose `requestId`, not `request_id`.
- Never expose ORM models, token fields, or credential-bearing persistence types directly.
- Endpoint tests assert public casing and response boundaries.

## Architecture

- `app/api/` owns HTTP routing and API versioning.
- `app/core/` owns configuration, logging, middleware, lifecycle, and exception handling.
- `app/db/` owns async SQLAlchemy setup, sessions, and database health checks.
- `app/domains/` owns reusable domain modules and their transaction-aware use cases.
- `app/services/` owns replaceable infrastructure and provider adapters.
- `app/shared/` owns reusable application primitives.
- Domain code receives dependencies through typed interfaces; it does not construct engines or infrastructure clients.
- Use-case/application services own transaction boundaries when multiple writes must be atomic.

## FastAPI Conventions

- Use `create_app()` from `app/main.py`.
- Keep `/health` as unversioned liveness and dependency checks in readiness endpoints.
- Keep versioned routes under `API_PREFIX`, default `/api/v1`.
- Use centralized exception handling for consistent error responses.
- Propagate request, trace, actor, and tenant context into asynchronous work without exposing secrets.
- Manage database, cache, queue, storage, and telemetry resources through application lifespan hooks.

## Database And Migrations

- Use async SQLAlchemy 2.x with the shared async engine and session utilities.
- Use PostgreSQL in production and PostgreSQL-backed integration tests for database-specific behavior.
- Use Alembic for migrations; migrations run as an explicit deployment step, not during API replica startup.
- Test `alembic upgrade head` in CI.
- Inspect generated migrations and preserve downgrade behavior unless an irreversible migration is explicitly documented.
- Do not create engines or sessions in domain code.

## Jobs And Side Effects

- Stable task names form the producer/worker contract; do not expose arbitrary Python import paths.
- Route jobs explicitly to configured queues and ensure deployed workers consume those queues.
- Define retryability, backoff, timeout, idempotency, and dead-letter behavior for side effects.
- Prefer a transactional outbox when a committed database change requires guaranteed publication.
- Job payloads contain identifiers and artifact references rather than large or sensitive objects.
- Correlation and trace metadata must survive enqueue and execution.

## Provider-Neutral AI Primitives

- Generic AI support may define invocation, structured output, streaming, embeddings, usage, and test-double contracts.
- Keep provider SDK details inside adapters and make heavyweight provider dependencies optional where practical.
- Routing, retry, fallback, concurrency, and budget policies must be explicit and observable.
- Persist or emit model, provider, latency, usage, cost estimate, and failure category without recording sensitive prompts by default.
- Required tests use deterministic fake providers or sanitized recordings, never live calls.
- Product prompts, tools, evaluations, safety thresholds, and agent workflows remain downstream concerns.

## Security And Tenancy

- Hash opaque tokens at rest and never log secrets or credentials.
- Browser cookie authentication requires explicit HttpOnly, Secure, SameSite, rotation, and CSRF design.
- Reusable authorization primitives may live here; downstream workspace or organization semantics do not.
- Tenant-aware downstream repositories must make tenant context mandatory rather than optional.

## Verification

After backend changes, run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy app tests
uv run pytest --cov
```

- Run PostgreSQL integration and Alembic checks for database or migration changes.
- Add negative authorization tests for auth or tenancy primitives.
- Add retry, duplicate-delivery, and idempotency tests for jobs and webhooks.
- Keep the Docker image buildable after dependency or startup changes.
