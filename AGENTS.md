# Shiptawk Backend Engineering Guide

## Authority And Scope

- This repository is Shiptawk's FastAPI product backend, derived from the reusable boilerplate identified in `BOILERPLATE_PROVENANCE.md`.

## Product Boundaries

- Put Shiptawk product modules under `app/modules/`; do not place product behavior in generic infrastructure modules.
- Extend an existing aggregate through its owning module's responsibility packages. Product profiles, product repositories, product audit history, and other product-only lifecycles belong directly in `app/modules/products/models/`, `schemas/`, `repositories/`, `services/`, `policies/`, `enums/`, and `api/`. Do not create nested domain modules such as `products/profiles/`. Create a top-level module only when the domain has independent ownership, authorization, persistence lifecycle, and use cases beyond a single parent aggregate.
- Name non-model implementation files with their responsibility suffix so ownership is visible at the import site: `*_router.py`, `*_repository.py`, `*_service.py`, `*_schema.py`, `*_policy.py`, and `*_enum.py`. Package `__init__.py` files contain no implementation logic.
- Name model files after the singular form of their table without a responsibility suffix. For example, `product_profiles` maps to `ProductProfile` in `product_profile.py`, and `product_profile_audit_events` maps to `ProductProfileAuditEvent` in `product_profile_audit_event.py`.
- Organize each module by responsibility using only the packages it needs: `api/`, `models/`, `schemas/`, `repositories/`, `services/`, `policies/`, `events/`, `detectors/`, `enums/`, and `providers/`.
- Keep SQLAlchemy definitions in the owning module's `models/`; do not recreate a cross-domain persistence registry.
- Repositories perform workspace-scoped queries and flushes. Services own application use cases and transaction boundaries. Policies and detectors remain pure.
- Put external provider protocols, HTTP/SDK implementations, and deterministic fakes under the owning module's `providers/`.
- Do not add flat `router.py`, `models.py`, `schemas.py`, `repository.py`, `service.py`, or `provider.py` files at a module root, and do not add runtime import aliases for obsolete paths.
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

- Batch verification after a coherent implementation slice. Do not run tests after every individual service, schema, or route change unless diagnosing a specific failure.

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
