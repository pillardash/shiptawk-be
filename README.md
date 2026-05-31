FastAPI Backend Boilerplate
===========================

Reusable FastAPI backend boilerplate for scalable PostgreSQL-backed APIs.

## Requirements

- Python 3.12
- uv
- PostgreSQL

## Setup

```bash
uv sync
cp .env.example .env
```

Use `DATABASE_URL` in `.env` for your local PostgreSQL instance.

## Run

```bash
uv run fastapi dev app/main.py
```

Run with Docker Compose:

```bash
docker compose up --build
```

Health checks:

- `GET /health`
- `GET /api/v1/health`
- `GET /api/v1/ready`

Auth endpoints:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/change-password`
- `POST /api/v1/auth/forgot-password`
- `POST /api/v1/auth/reset-password`
- `POST /api/v1/auth/email-verification/request`
- `POST /api/v1/auth/email-verification/verify`

## Test and Lint

```bash
make check

uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
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
- `FORWARDED_ALLOW_IPS` controls trusted proxy IPs for forwarded headers.
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
- `/api/v1/ready` is readiness and checks PostgreSQL.

## Database

- The project uses sync SQLAlchemy with PostgreSQL.
- Engine pool settings are configured through `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT`, and `DATABASE_POOL_RECYCLE`.
- Future SQLAlchemy models should inherit from `app.db.base.BaseModel` by default.
- `BaseModel` provides a UUID primary key, `created_at`, `updated_at`, `created_by`, `updated_by`, and `deleted_at` columns.
- `created_by` and `updated_by` are nullable UUID audit columns without foreign keys, so the boilerplate remains decoupled from any future auth/user domain.
- `deleted_at` is a nullable indexed timestamp for soft deletes. Domain queries should filter it out unless they intentionally include deleted rows.
- Use `get_db()` for FastAPI dependencies and `session_scope()` for scripts/services that need an explicit transaction boundary.
- Alembic autogenerate uses stable constraint naming conventions and type comparison.

## API Contract

- Public request and response payloads use camelCase.
- Python internals use snake_case.
- Public Pydantic schemas should inherit from `app.shared.schemas.ApiSchema`.
- Use `model_dump(by_alias=True)` when manually serializing shared schemas.
- Error responses use `requestId`, not `request_id`.
- Pagination primitives live in `app.shared.pagination` and serialize fields such as `pageSize`, `totalItems`, and `hasNext`.

## Auth and Users

- The boilerplate includes a reusable email/password auth foundation.
- Passwords are hashed with Argon2 via `pwdlib`.
- JWT access tokens are signed with `JWT_SECRET_KEY`.
- Registration and login return the authenticated user plus `accessToken` and `tokenType`.
- Refresh/logout use DB-backed `auth_sessions` for revocation and session metadata.
- Register, login, and refresh accept optional `deviceName`; `User-Agent` and IP are captured automatically.
- Password reset and email verification use one-time hashed `auth_tokens`.
- Login and password reset flows include cache-backed throttling. Use Redis-backed cache for distributed deployments.
- User API responses never expose `passwordHash` or `password_hash`.
- Set a strong `JWT_SECRET_KEY` before deploying. The default value is rejected in production.

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
  domains/   future business domains
  shared/    reusable application primitives
```

The first implementation pass intentionally includes infrastructure only. Domain logic and auth should be added as separate focused modules.
