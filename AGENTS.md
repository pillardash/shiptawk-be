# Shiptawk Backend Engineering Guide

## Authority And Scope

- This repository is Shiptawk's FastAPI product backend, derived from the reusable boilerplate identified in `BOILERPLATE_PROVENANCE.md`.
- The parent `../AGENTS.md` and `../docs/ai-marketing-operator-conversion-plan.md` govern cross-repository product and migration decisions.
- This backend owns authentication, authorization, workspace tenancy, persistence, migrations, integrations, webhooks, durable workflows, AI execution, approvals, publishing policy, and audit trails.
- Keep the backend independently runnable and testable from the Next.js frontend.

## Product Boundaries

- Put Shiptawk domains under `app/domains/`; do not place product behavior in generic infrastructure modules.
- Inngest is the current durable workflow authority. Backend ingestion publishes metadata-only events; Inngest functions orchestrate and domain services own decisions and invariants.
- Temporal is deferred. Do not add a parallel Temporal execution path without an explicit Inngest replacement and cutover plan.
- Product prompts, evidence policies, evaluations, and model-routing policy live here, not in the reusable boilerplate.
- During migration, each use case has exactly one mutation and AI execution authority.
- Do not enable a FastAPI write path until its live schema, ownership, idempotency, rollout, and rollback behavior are reconciled.

## Tenancy And Security

- Require workspace context in every tenant-aware repository method.
- Authorize membership server-side before accessing workspace resources.
- Add negative cross-workspace tests for every new resource type.
- Never expose ORM models, encrypted credentials, provider tokens, or persistence-only fields through API schemas.
- Preserve compatibility with existing encrypted credentials until migration is complete.
- Never log or send credentials, private source, unrestricted payloads, or raw sensitive prompts to providers.

## Contracts

- Public HTTP APIs are versioned and use camelCase through `ApiSchema`.
- Python internals use snake_case.
- Generate the frontend TypeScript client from OpenAPI; do not maintain handwritten duplicate contracts.
- Make contract migrations additive and preserve compatibility during frontend adoption.
- Version cross-process events and JSON payloads explicitly; use stable IDs and idempotency keys.

## AI And Workflows

- Keep AI provider SDKs behind provider-neutral adapters.
- Separate use cases, prompt versions, transport, schema validation, routing/fallback, durable execution, and telemetry.
- Validate structured model output before domain use; malformed output is a controlled failure.
- Persist provider, model, prompt version, parameters, latency, usage, cost estimate, status, and correlation IDs without sensitive prompt logging.
- Classify retryable transport failures separately from invalid output.
- Make fallback explicit and observable.
- Required tests use deterministic fakes or sanitized recordings, never live providers.
- Treat insufficient evidence as a valid no-recommendation outcome.

## Database And Migrations

- Use shared async SQLAlchemy sessions and PostgreSQL.
- Use Alembic for migrations and test upgrades against PostgreSQL.
- Reconcile the live Supabase schema before enabling migrated writes.
- Do not use production dual writes.
- Preserve existing IDs, history, external-delivery identifiers, and publishing receipts.

## Boilerplate Synchronization

- Generic fixes should be implemented and verified in `/home/mrprotocoll/Documents/projects/fastapi-boilerplate` first when practical.
- Upstream product-proven abstractions only after removing Shiptawk assumptions and fixtures.
- Keep upstream candidates isolated. Never merge this product tree wholesale into the boilerplate.
- Update `BOILERPLATE_PROVENANCE.md` when intentionally refreshing the derived baseline.
- This repository must not track the sibling frontend or parent coordination files.

## Verification

Run after backend changes:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy app tests
uv run pytest --cov
```

- Run Alembic upgrades against PostgreSQL for migration changes.
- Add API contract tests for endpoint changes.
- Add duplicate-delivery and retry tests for jobs, webhooks, and side effects.
- Keep the Docker image buildable after dependency or startup changes.
