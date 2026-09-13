Shiptawk Backend
================

FastAPI backend for the Shiptawk AI marketing operator, derived from the reusable
FastAPI boilerplate documented in `BOILERPLATE_PROVENANCE.md`.

Cross-repository architecture and migration plans live in the parent `../docs`
directory. Product behavior belongs here; reusable infrastructure improvements should
be synchronized selectively with the boilerplate.

## Requirements

- Python 3.12
- uv
- PostgreSQL

## Setup

```bash
uv sync
cp .env.example .env
```

Use an async SQLAlchemy URL such as
`postgresql+psycopg://postgres:postgres@localhost:5432/app` for `DATABASE_URL` in `.env`.

## Run

```bash
uv run uvicorn app.main:app --reload
```

Run with Docker Compose:

```bash
docker compose up --build
```

Health checks:

- `GET /health`
- `GET /v1/health`
- `GET /v1/ready`

Auth endpoints:

- `GET /v1/auth/me`
- `GET /v1/auth/browser/providers`
- `GET /v1/auth/browser/oauth/{provider}/authorize`
- `GET /v1/auth/browser/oauth/{provider}/callback`
- `GET /v1/auth/browser/session`
- `POST /v1/auth/browser/refresh`
- `POST /v1/auth/browser/logout`
- `DELETE /v1/auth/browser/account`
- `POST /v1/workspaces/{workspaceId}/github/installation`
- `POST /v1/workspaces/{workspaceId}/repositories/sync`
- `GET /v1/workspaces/{workspaceId}/repositories`
- `PATCH /v1/workspaces/{workspaceId}/repositories/{repositoryId}/tracking`
- `GET /v1/workspaces/{workspaceId}/github/installation`
- `POST /v1/webhooks/github` (public, GitHub HMAC authenticated)

## Test and Lint

```bash
make check

uv run pytest --cov
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests

# Deterministic Phase 4 opportunity-engine release gates
uv run pytest -q tests/test_opportunity_engine_evaluation.py
```

Useful development commands:

```bash
make dev
make format
make test-cov
make migrate
make revision message="create example table"
```

## Migrations

Alembic is configured to read the database URL from `DATABASE_URL`.

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

For deployments, run migrations before starting the API process:

```bash
./scripts/prestart.sh
./scripts/start.sh
```

Do not run migrations from FastAPI startup hooks. Keep schema changes as an explicit deployment step.

In Coolify, configure `./scripts/prestart.sh` as the pre-deploy command. The production image does
not include the `uv` executable, so do not use `uv run alembic upgrade head` inside the runtime
container. Run the pre-deploy command once per release before API replicas are replaced. The API
container command must remain `./scripts/start.sh`; never run migrations in that command or once
per replica.

## Deployment

Build the production image:

```bash
docker build -t backend-api .
```

Run the production container:

```bash
docker run --env-file .env -p 8000:8000 backend-api
```

Runtime settings:

- `PORT` controls the container listening port. Default: `8000`.
- `WORKERS` controls Uvicorn worker processes. Default: `1`.
- `GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS` controls how long in-flight requests may finish after
  Coolify sends `SIGTERM`. Default: `30`.
- `FORWARDED_ALLOW_IPS` controls trusted proxy IPs for forwarded headers. Production rejects `*`.
- `TRUSTED_PROXY_IPS` controls which proxy IPs may supply `X-Forwarded-For` for rate limiting and throttling.
- `ALLOWED_HOSTS` must not contain `*` in production.
- `CORS_ORIGINS` must list explicit origins when credentials are enabled.
- `APP_ENV=production` enables production config validation.
- `APP_ENV=production` disables OpenAPI docs by default. Set `OPENAPI_ENABLED=true` to override.
- `STORAGE_PROVIDER=local` uses `storage/` by default outside production. In production, set `STORAGE_LOCAL_PATH` explicitly.
- `CACHE_BACKEND=redis` requires `REDIS_URL`.
- `CACHE_BACKEND=redis` is required for distributed rate limiting across multiple production workers.
- `EMAIL_PROVIDER=smtp` requires `SMTP_HOST` and `EMAIL_FROM`; SMTP credentials must be provided together.
- In production, SMTP email requires `JOBS_BACKEND=rq` so auth requests do not block on SMTP delivery.
- `JOBS_BACKEND=rq` requires `JOBS_REDIS_URL` or `REDIS_URL`.
- `SENTRY_DSN` enables Sentry error reporting. Without `SENTRY_DSN`, Sentry is disabled.

Health endpoints:

- `/health` is liveness and does not check dependencies.
- `/v1/ready` is readiness and checks PostgreSQL.

## Database

- The project uses SQLAlchemy 2.x `AsyncSession` with PostgreSQL and psycopg's async driver.
- Engine pool settings are configured through `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT`, and `DATABASE_POOL_RECYCLE`.
- Future SQLAlchemy models should inherit from `app.db.base.BaseModel` by default.
- `BaseModel` provides a UUID primary key, `created_at`, `updated_at`, `created_by`, `updated_by`, and `deleted_at` columns.
- `created_by` and `updated_by` are nullable UUID audit columns without foreign keys, so the boilerplate remains decoupled from any future auth/user domain.
- `deleted_at` is a nullable indexed timestamp for soft deletes. Domain queries should filter it out unless they intentionally include deleted rows.
- Use async `get_db()` for FastAPI dependencies and `async with session_scope()` for scripts/services that need an explicit transaction boundary.
- Alembic autogenerate uses stable constraint naming conventions and type comparison.

## API Contract

- Public request and response payloads use camelCase.
- Python internals use snake_case.
- Public Pydantic schemas should inherit from `app.shared.schemas.ApiSchema`.
- Use `model_dump(by_alias=True)` when manually serializing shared schemas.
- Error responses use `requestId`, not `request_id`.
- Pagination primitives live in `app.shared.pagination` and serialize fields such as `pageSize`, `totalItems`, and `hasNext`.

## Auth and Users

- Shiptawk is OAuth-only. There are no public password registration, login, reset, or
  change-password routes, and no password hashes or password-token tables.
- JWT access tokens are signed with `JWT_SECRET_KEY`.
- Browser refresh/logout use DB-backed `auth_sessions` for revocation and session metadata.
- Session responses include the user, `currentWorkspace`, and `linkedProviders`; credentials
  and provider tokens are never returned.
- Set a strong `JWT_SECRET_KEY` before deploying. The default value is rejected in production.

### Browser OAuth and sessions

- OAuth providers implement `app.services.oauth.OAuthProvider` and are registered on the
  application's `OAuthProviderRegistry`. No provider SDK is required. `FakeOAuthProvider` is a
  deterministic test double and must not be registered in production.
- Authorization uses a SHA-256 state hash at rest, a short-lived one-time transaction, and PKCE
  S256. PKCE verifiers are authenticated-encrypted at rest with the explicit Fernet-compatible
  `OAUTH_TRANSACTION_ENCRYPTION_KEY`; provider access/refresh tokens are not persisted. Generate
  a key with `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
- Browser access JWTs include `sub`, `sid`, `iss`, `aud`, `iat`, `nbf`, `exp`, `jti`, and
  `type=access`. Browser routes do not accept bearer tokens.
- Refresh cookies contain opaque random values whose SHA-256 hashes are persisted. Rotation is
  session-family aware; replay marks the family compromised and revokes it.
- Unsafe browser cookie routes require an exact `Origin` from `BROWSER_ALLOWED_ORIGINS` and a
  signed, session-bound double-submit value in the readable CSRF cookie and configured header.
  Downstream unsafe routes should depend on the exported `CookieAuthCsrfDep` rather than copying
  checks. `BrowserSessionDep` is available for safe cookie-authenticated reads.
- Keep `BROWSER_COOKIE_DOMAIN` empty for host-only cookies. For sibling subdomains, set a shared
  parent such as `.example.com`; this deliberately broadens which hosts receive the cookie.
  Production should use secure cookies. `SameSite=None` is rejected unless secure is enabled.
- Access and CSRF cookies use `/` so frontend JavaScript can read the CSRF value; the HttpOnly
  refresh cookie remains restricted to browser-auth routes.
  Names are configurable for `__Host-`/`__Secure-` conventions, but operators must choose names
  compatible with their configured domain and path.
- `OAuthIdentityData.email_verified` is trusted only after an adapter validates the provider
  response. Accounts are never auto-linked by email.
- OAuth callback state consumption commits before provider I/O. After provider validation, user,
  identity metadata, and refresh-session creation commit atomically in one database transaction.
- OAuth and browser-session responses use `Cache-Control: no-store`. Successful callbacks always
  redirect to `FRONTEND_URL` plus the validated relative return path, including its query string.
- Logout requires exact Origin and session-bound CSRF and expires all browser session cookies.
- Account deletion is a CSRF-protected soft deletion. It revokes all sessions and OAuth identities,
  clears browser cookies, removes directly identifying profile fields, and retains tenant content
  and audit history for migration/compliance. A retained soft-deleted provider identity is not
  automatically restored; restoration requires a future explicit administrative policy.
- Protect the database and delete expired OAuth transactions regularly. Provider tokens, raw
  OAuth state, and plaintext PKCE verifiers are never stored.

### Coolify production environment

Create a Dockerfile application in Coolify with the repository root set to this backend directory.
Use these deployment settings:

- Dockerfile: `Dockerfile`
- Exposed container port: `8000`
- Health check path: `/health`
- Pre-deploy command: `./scripts/prestart.sh`
- Start command: leave empty to use the image command `./scripts/start.sh`
- Database: attach a Coolify-managed PostgreSQL resource and set its internal connection string as
  `DATABASE_URL`, using the `postgresql+psycopg://` scheme
- Readiness check after deployment: `/v1/ready`

Do not expose the managed PostgreSQL port publicly. The application and database must share a
Coolify network, and `DATABASE_URL` must use the database resource's internal hostname rather than
`localhost`.

For `app.shiptawk.com` and `api.shiptawk.com`, configure these exact values in Coolify in
addition to the ordinary production database, JWT, storage, proxy, and cache settings:

```dotenv
APP_ENV=production
FRONTEND_URL=https://app.shiptawk.com
PUBLIC_BACKEND_URL=https://api.shiptawk.com
ALLOWED_HOSTS=api.shiptawk.com
CORS_ORIGINS=https://app.shiptawk.com
BROWSER_ALLOWED_ORIGINS=https://app.shiptawk.com
BROWSER_COOKIE_DOMAIN=.shiptawk.com
BROWSER_COOKIE_SECURE=true
BROWSER_COOKIE_SAMESITE=lax
OAUTH_ENABLED_PROVIDERS=github
OAUTH_TRANSACTION_ENCRYPTION_KEY=<fernet-key>
GITHUB_OAUTH_CLIENT_ID=<github-oauth-app-client-id>
GITHUB_OAUTH_CLIENT_SECRET=<github-oauth-app-client-secret>
GITHUB_APP_ID=<github-app-id>
GITHUB_APP_PRIVATE_KEY=<github-app-private-key-pem>
GITHUB_WEBHOOK_ENABLED=true
GITHUB_WEBHOOK_SECRET=<independent-high-entropy-webhook-secret>
GITHUB_WEBHOOK_PAYLOAD_ENCRYPTION_KEY=<fernet-key>
GITHUB_WEBHOOK_MAX_BODY_BYTES=1048576
PROXY_HEADERS=true
FORWARDED_ALLOW_IPS=<coolify-proxy-ip-or-private-cidr>
TRUSTED_PROXY_IPS=<coolify-proxy-ip-or-private-cidr>
```

Restrict both proxy settings to Coolify's actual internal proxy IP or private network CIDR. Do not
use `*`; request hosts and OAuth callback URIs are not derived from forwarded headers. The callback
always uses `PUBLIC_BACKEND_URL`.

The GitHub App is separate from GitHub login OAuth. The backend mints a short-lived app JWT and
installation token only when calling GitHub, stores the verified installation as a workspace
integration, and never returns installation tokens. Newly discovered repositories are untracked
until an authenticated workspace member explicitly enables tracking.

Set the GitHub OAuth callback URL to
`https://api.shiptawk.com/v1/auth/browser/oauth/github/callback`. Local development keeps
host-only insecure `Lax` cookies and uses `http://localhost:3000` by default.

Set the Google Search Console OAuth callback exactly to
`http://localhost:8000/v1/integrations/search/google/callback` for local development and
`https://api.shiptawk.com/v1/integrations/search/google/callback` for production. The callback
is backend-owned and returns a `307` to the validated, product-bound frontend `returnPath`, replacing
any existing `search` query value with `connected`, `denied`, or `error`. The frontend must not host
or exchange codes at a separate Search Console callback.

Set the GitHub App webhook URL to the canonical production URL
`https://api.shiptawk.com/v1/webhooks/github` and configure the exact same secret in GitHub
and `GITHUB_WEBHOOK_SECRET`. Generate the payload key independently with
`uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
The endpoint authenticates the original bounded request bytes before parsing, stores supported
deliveries as encrypted global ingress records, and creates pending consumers only for repositories
that were explicitly tracked. Unsupported events are acknowledged and ignored. It does not run AI,
provider, or publishing work inline.

### Backend Inngest workflows

FastAPI serves the authoritative Inngest functions at `/api/inngest`. Webhook ingestion commits the
encrypted global event and workspace consumers before publishing one idempotent
`github/event.received` event per consumer. Event data contains only `consumerId`, `schemaVersion`,
and `correlationId`; raw payloads are decrypted only inside the backend workflow. The backend also
owns onboarding events and weekly/monthly digest schedules. Functions use stable event IDs,
database idempotency, and bounded Inngest retries. Temporal must not run in parallel.

Production requires independent Inngest keys, the webhook Fernet key, and a configured generation
provider:

```dotenv
INNGEST_ENABLED=true
INNGEST_EVENT_KEY=<inngest-event-key>
INNGEST_SIGNING_KEY=<inngest-signing-key>
GENERATION_PROVIDER=openai
OPENAI_API_KEY=<provider-key>
```

Before setting `GITHUB_WEBHOOK_ENABLED=true`, run `./scripts/prestart.sh` as Coolify's single
pre-deploy migration command and confirm the `github_raw_events` and
`github_raw_event_consumers` tables exist. Enable this endpoint only at the coordinated webhook
authority cutover; do not leave the legacy Next.js webhook writing concurrently. Keep
`./scripts/start.sh` as the API command so migrations do not race across replicas.

## Initial product schema

- `0001_initial_product_schema` is a predeployment-only clean baseline for the planned Supabase
  reset. It must not be substituted for an additive migration after any backend deployment.
- Every tenant-owned product and legacy workflow table has a non-null `workspace_id`.
- Legacy `user_id`, `users.github_id`, and `users.github_username` remain only as temporary,
  non-secret frontend migration fields. Authorization must use workspace membership.
- User-level GitHub/Twitter token columns do not exist. OAuth login tokens are discarded after
  identity retrieval. Durable integration credentials belong encrypted in
  `integration_connections`.
- `social_accounts` remains an isolated temporary plaintext-compatible bridge because the active
  frontend still reads and writes it. Do not expose it through FastAPI; migrate it to encrypted
  `integration_connections` before retiring the frontend database path.
- The baseline already contains the restricted GitHub ingress and consumer tables required by the
  FastAPI webhook. Apply it before enabling webhook traffic; after the first backend deployment,
  all further schema changes must use additive Alembic revisions rather than editing the baseline.

## Infrastructure Services

- Cache access goes through `app.services.cache.CacheService`; Redis-specific code lives under `app/cache/`.
- Email delivery goes through `app.services.email.EmailService`; SMTP-specific code lives in `app/services/email/smtp.py`.
- Object storage goes through `app.services.storage.StorageService`; local and S3-compatible providers are isolated.
- Background jobs go through `app.services.jobs.JobService`; RQ-specific code is isolated and worker startup is `uv run python -m app.jobs.worker`.
- Local storage paths are never returned from API helpers; local storage `url()` returns `None`.
- The first job implementation uses the default queue. Category queue settings exist for future separate workers.

## Architecture

```text
app/
  api/       HTTP routers and API versioning
  core/      settings, logging, middleware, and exception handling
  db/        SQLAlchemy base, engine, and session dependencies
  modules/   product capabilities grouped as vertical modules
  services/  provider-neutral infrastructure adapters
  workflows/ durable product-workflow orchestration
  shared/    reusable application primitives
```

Product behavior belongs in focused modules. Routers and workflows orchestrate module services rather than owning domain decisions.

Each product module creates only the responsibility packages it uses:

```text
<module>/
  api/           HTTP routing and response mapping
  models/        module-owned SQLAlchemy definitions
  schemas/       Pydantic contracts
  repositories/  workspace-scoped database access
  services/      application use cases and transactions
  policies/      pure permission and state rules
  events/        versioned message contracts
  detectors/     deterministic detection and scoring
  enums/         shared domain vocabulary
  providers/     external provider protocols, adapters, and fakes
```

Unused responsibility packages are not created. See `../architeture.md` for the dependency rules and module ownership map.
# Browser OAuth

Browser sign-in providers are enabled with `OAUTH_ENABLED_PROVIDERS` (for example,
`google,github`). Google login uses the dedicated `GOOGLE_LOGIN_CLIENT_ID` and
`GOOGLE_LOGIN_CLIENT_SECRET` credentials and this callback:

`{PUBLIC_BACKEND_URL}{API_PREFIX}/auth/browser/oauth/google/callback`

Register the exact callback in the Google OAuth client. Login requests only
`openid email profile`; it does not request Search Console or offline access. Search Console is a
separate integration configured with `GOOGLE_SEARCH_ENABLED`, `GOOGLE_SEARCH_CLIENT_ID`, and
`GOOGLE_SEARCH_CLIENT_SECRET`, using
`{PUBLIC_BACKEND_URL}{API_PREFIX}/integrations/search/google/callback`.

OAuth accounts are keyed by provider and provider subject. Matching email addresses across Google
and GitHub are intentionally not linked and create separate accounts.

## Email workflow schedules

The canonical weekly growth operator is the MVP scheduling authority. Its hourly due-product scan is
controlled independently with `WEEKLY_GROWTH_SCHEDULE_ENABLED` and defaults to enabled. The
event-driven `operator/weekly-growth.ready` email function remains registered regardless of that
schedule gate so manual and already-enqueued runs can still deliver.

The retained legacy schedules default to disabled and can be restored individually for rollback with
`LEGACY_DAILY_DRAFT_SCHEDULE_ENABLED`, `LEGACY_ACHIEVEMENT_DIGEST_SCHEDULES_ENABLED`, and
`LEGACY_REPOSITORY_CHANGELOG_SCHEDULES_ENABLED`. These flags change only Inngest function
registration; the underlying delivery implementations and user-level preferences remain intact.
