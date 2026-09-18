from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal, cast
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import EmailStr, Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "production"]
LogFormat = Literal["console", "json"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
EmailProvider = Literal["smtp"]
GenerationProviderName = Literal["openai"]
StorageProvider = Literal["local", "s3"]
JobsBackend = Literal["inline", "rq"]
CookieSameSite = Literal["lax", "strict", "none"]


class Settings(BaseSettings):
    app_name: str = "Backend API"
    app_version: str = "0.1.0"
    app_env: AppEnv = "local"
    debug: bool = False
    api_v1_prefix: str = Field(default="/v1", alias="API_PREFIX")
    frontend_url: str = "http://localhost:3000"
    public_backend_url: str = "http://localhost:8000"
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    port: int = 8000
    workers: int = 1
    proxy_headers: bool = True
    forwarded_allow_ips: str = "*"
    trusted_proxy_ips: Annotated[list[str], NoDecode] = Field(default_factory=list)

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/app"
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_pool_recycle: int = 1800
    database_echo: bool = False

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:3001",
        ]
    )
    cors_allow_credentials: bool = True
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "console"
    jwt_secret_key: str = "change-me"
    token_encryption_key: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    jwt_issuer: str = "backend-api"
    jwt_audience: str = "backend-api"
    oauth_transaction_expire_minutes: int = 10
    oauth_transaction_encryption_key: SecretStr | None = None
    oauth_enabled_providers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: SecretStr | None = None
    google_login_client_id: str | None = None
    google_login_client_secret: SecretStr | None = None
    x_oauth_enabled: bool = False
    x_publishing_enabled: bool = False
    x_oauth_client_id: str | None = None
    x_oauth_client_secret: SecretStr | None = None
    x_oauth_scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["tweet.read", "tweet.write", "users.read", "offline.access"]
    )
    integration_credentials_encryption_key: SecretStr | None = None
    integration_credentials_active_key_version: str = "v1"
    integration_credentials_previous_encryption_keys: dict[str, SecretStr] = Field(
        default_factory=dict
    )
    google_search_enabled: bool = False
    google_search_client_id: str | None = None
    google_search_client_secret: SecretStr | None = None
    github_app_id: int | None = None
    github_app_private_key: SecretStr | None = None
    github_webhook_enabled: bool = False
    github_webhook_secret: SecretStr | None = None
    github_webhook_payload_encryption_key: SecretStr | None = None
    github_webhook_max_body_bytes: int = 1_048_576
    inngest_enabled: bool = False
    inngest_event_key: SecretStr | None = None
    inngest_signing_key: SecretStr | None = None
    inngest_event_api_base_url: str | None = None
    inngest_request_timeout_seconds: float = 10.0
    legacy_daily_draft_schedule_enabled: bool = False
    legacy_achievement_digest_schedules_enabled: bool = False
    legacy_repository_changelog_schedules_enabled: bool = False
    weekly_growth_schedule_enabled: bool = True
    generation_provider: GenerationProviderName | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_input_price_per_million: Decimal = Decimal("0.40")
    openai_output_price_per_million: Decimal = Decimal("1.60")
    openai_pricing_version: str = "openai-2025-04-14"
    generation_timeout_seconds: float = 30.0
    oauth_http_timeout_seconds: float = 10.0
    browser_cookie_secure: bool = False
    browser_cookie_domain: str | None = None
    browser_cookie_samesite: CookieSameSite = "lax"
    browser_access_cookie_name: str = "access_token"
    browser_refresh_cookie_name: str = "refresh_token"
    browser_refresh_cookie_path: str = "/"
    browser_csrf_cookie_name: str = "csrf_token"
    browser_csrf_header_name: str = "X-CSRF-Token"
    browser_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    openapi_enabled: bool | None = None
    docs_enabled: bool | None = None
    redoc_enabled: bool | None = None
    max_request_body_bytes: int = 1_048_576
    security_headers_enabled: bool = True
    hsts_enabled: bool | None = None
    hsts_max_age_seconds: int = 31_536_000
    referrer_policy: str = "no-referrer"
    permissions_policy: str = "geolocation=(), microphone=(), camera=()"
    rate_limit_enabled: bool = True
    cache_backend: Literal["memory", "redis"] = "memory"
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    redis_url: str | None = None
    expose_error_details: bool | None = None
    email_provider: EmailProvider | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    email_from: EmailStr | None = None
    storage_provider: StorageProvider = "local"
    storage_local_path: str | None = None
    storage_bucket: str | None = None
    storage_endpoint: str | None = None
    storage_access_key: str | None = None
    storage_secret_key: str | None = None
    storage_region: str = "us-east-1"
    storage_public_base_url: str | None = None
    storage_presigned_url_expire_seconds: int = 900
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0
    sentry_profiles_sample_rate: float = 0.0
    sentry_send_default_pii: bool = False
    jobs_backend: JobsBackend = "inline"
    jobs_queue_name: str = "default"
    jobs_redis_url: str | None = None
    jobs_default_timeout_seconds: int = 300
    jobs_result_ttl_seconds: int = 86_400
    jobs_failure_ttl_seconds: int = 604_800
    jobs_email_queue: str = "email"
    jobs_files_queue: str = "files"
    jobs_ai_queue: str = "ai"
    jobs_reports_queue: str = "reports"
    jobs_webhooks_queue: str = "webhooks"
    jobs_notifications_queue: str = "notifications"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str] | str:
        if isinstance(value, str) and value:
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("browser_allowed_origins", mode="before")
    @classmethod
    def parse_browser_allowed_origins(cls, value: str | list[str]) -> list[str] | str:
        if isinstance(value, str) and value:
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("oauth_enabled_providers", mode="before")
    @classmethod
    def parse_oauth_enabled_providers(cls, value: str | list[str]) -> list[str] | str:
        if isinstance(value, str):
            return [provider.strip().lower() for provider in value.split(",") if provider.strip()]
        return value

    @field_validator("x_oauth_scopes", mode="before")
    @classmethod
    def parse_x_oauth_scopes(cls, value: str | list[str]) -> list[str] | str:
        if isinstance(value, str):
            return [scope.strip() for scope in value.replace(",", " ").split() if scope.strip()]
        return value

    @field_validator("oauth_transaction_encryption_key", mode="before")
    @classmethod
    def validate_oauth_transaction_encryption_key(
        cls, value: SecretStr | str | None
    ) -> SecretStr | str | None:
        if value is None or value == "":
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        try:
            Fernet(raw_value.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise ValueError("OAUTH_TRANSACTION_ENCRYPTION_KEY must be a Fernet key.") from exc
        return value

    @field_validator("integration_credentials_encryption_key", mode="before")
    @classmethod
    def validate_integration_credentials_encryption_key(
        cls, value: SecretStr | str | None
    ) -> SecretStr | str | None:
        if value is None or value == "":
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        try:
            Fernet(raw_value.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise ValueError(
                "INTEGRATION_CREDENTIALS_ENCRYPTION_KEY must be a Fernet key."
            ) from exc
        return value

    @field_validator("integration_credentials_previous_encryption_keys")
    @classmethod
    def validate_previous_integration_credential_keys(
        cls, value: dict[str, SecretStr]
    ) -> dict[str, SecretStr]:
        for version, secret in value.items():
            if not version.strip():
                raise ValueError("Integration credential key versions must not be empty.")
            try:
                Fernet(secret.get_secret_value().encode("ascii"))
            except (UnicodeEncodeError, ValueError) as exc:
                raise ValueError(
                    "INTEGRATION_CREDENTIALS_PREVIOUS_ENCRYPTION_KEYS values must be Fernet keys."
                ) from exc
        return value

    @field_validator("github_webhook_payload_encryption_key", mode="before")
    @classmethod
    def validate_github_webhook_payload_encryption_key(
        cls, value: SecretStr | str | None
    ) -> SecretStr | str | None:
        if value is None or value == "":
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        try:
            Fernet(raw_value.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise ValueError("GITHUB_WEBHOOK_PAYLOAD_ENCRYPTION_KEY must be a Fernet key.") from exc
        return value

    @field_validator("github_webhook_secret", mode="before")
    @classmethod
    def validate_github_webhook_secret(
        cls, value: SecretStr | str | None
    ) -> SecretStr | str | None:
        if value is None:
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not raw_value:
            return None
        return value

    @field_validator("inngest_event_key", "inngest_signing_key", "openai_api_key", mode="before")
    @classmethod
    def normalize_optional_secrets(cls, value: SecretStr | str | None) -> SecretStr | str | None:
        if value is None:
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        return value if raw_value else None

    @field_validator("browser_allowed_origins")
    @classmethod
    def validate_browser_allowed_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if not origin.startswith(("http://", "https://")) or origin.endswith("/"):
                raise ValueError("Browser allowed origins must be absolute origins without paths.")
            from urllib.parse import urlsplit

            parsed = urlsplit(origin)
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment or not parsed.netloc:
                raise ValueError("Browser allowed origins must be exact HTTP(S) origins.")
        return value

    @field_validator("browser_cookie_domain")
    @classmethod
    def validate_browser_cookie_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if (
            not normalized.strip(".")
            or any(char.isspace() for char in normalized)
            or any(char in normalized for char in "/:")
        ):
            raise ValueError("BROWSER_COOKIE_DOMAIN must be a DNS domain, not a URL.")
        return normalized

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if origin == "*":
                continue
            if not origin.startswith(("http://", "https://")):
                raise ValueError("CORS origins must be absolute HTTP(S) origins or '*'.")
        return value

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, value: str | list[str]) -> list[str] | str:
        if isinstance(value, str) and value:
            return [host.strip() for host in value.split(",") if host.strip()]
        return value

    @field_validator("trusted_proxy_ips", mode="before")
    @classmethod
    def parse_trusted_proxy_ips(cls, value: str | list[str]) -> list[str] | str:
        if isinstance(value, str) and value:
            return [host.strip() for host in value.split(",") if host.strip()]
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> LogLevel:
        return cast(LogLevel, value.upper())

    @field_validator("log_format", mode="before")
    @classmethod
    def normalize_log_format(cls, value: str) -> LogFormat:
        return cast(LogFormat, value.lower())

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("API_PREFIX must start with '/'.")
        return value.rstrip("/") or "/"

    @field_validator("frontend_url")
    @classmethod
    def validate_frontend_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("FRONTEND_URL must be an absolute HTTP(S) URL.")
        return value.rstrip("/")

    @field_validator("public_backend_url")
    @classmethod
    def validate_public_backend_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("PUBLIC_BACKEND_URL must be a canonical HTTP(S) origin.")
        return value.rstrip("/")

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("PORT must be between 1 and 65535.")
        return value

    @field_validator("workers")
    @classmethod
    def validate_workers(cls, value: int) -> int:
        if value < 1:
            raise ValueError("WORKERS must be at least 1.")
        return value

    @field_validator(
        "max_request_body_bytes",
        "hsts_max_age_seconds",
        "rate_limit_requests",
        "rate_limit_window_seconds",
        "oauth_transaction_expire_minutes",
        "storage_presigned_url_expire_seconds",
        "jobs_default_timeout_seconds",
        "jobs_result_ttl_seconds",
        "jobs_failure_ttl_seconds",
        "github_webhook_max_body_bytes",
    )
    @classmethod
    def validate_positive_int(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Security numeric settings must be positive.")
        return value

    @field_validator("smtp_port")
    @classmethod
    def validate_smtp_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535.")
        return value

    @field_validator("sentry_traces_sample_rate", "sentry_profiles_sample_rate")
    @classmethod
    def validate_sample_rate(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("Sentry sample rates must be between 0.0 and 1.0.")
        return value

    @field_validator("inngest_request_timeout_seconds", "generation_timeout_seconds")
    @classmethod
    def validate_workflow_timeout(cls, value: float) -> float:
        if value <= 0 or value > 120:
            raise ValueError("Workflow timeouts must be greater than 0 and at most 120 seconds.")
        return value

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        if self.cache_backend == "redis" and not self.redis_url:
            raise ValueError("REDIS_URL is required when CACHE_BACKEND=redis.")
        if self.browser_cookie_samesite == "none" and not self.browser_cookie_secure:
            raise ValueError("BROWSER_COOKIE_SECURE=true is required with SameSite=None.")
        if (
            self.app_env == "production"
            and self.oauth_enabled_providers
            and self.oauth_transaction_encryption_key is None
        ):
            raise ValueError(
                "OAUTH_TRANSACTION_ENCRYPTION_KEY is required when OAuth providers are enabled "
                "in production."
            )
        unknown_providers = set(self.oauth_enabled_providers) - {"github", "google"}
        if unknown_providers:
            raise ValueError(f"Unsupported OAuth providers: {', '.join(sorted(unknown_providers))}")
        if "github" in self.oauth_enabled_providers:
            if not self.github_oauth_client_id:
                raise ValueError("GITHUB_OAUTH_CLIENT_ID is required when GitHub OAuth is enabled.")
            if self.github_oauth_client_secret is None:
                raise ValueError(
                    "GITHUB_OAUTH_CLIENT_SECRET is required when GitHub OAuth is enabled."
                )
        if "google" in self.oauth_enabled_providers:
            if not self.google_login_client_id:
                raise ValueError("GOOGLE_LOGIN_CLIENT_ID is required when Google login is enabled.")
            if self.google_login_client_secret is None:
                raise ValueError(
                    "GOOGLE_LOGIN_CLIENT_SECRET is required when Google login is enabled."
                )
        if self.x_oauth_enabled:
            if not self.x_oauth_client_id:
                raise ValueError("X_OAUTH_CLIENT_ID is required when X OAuth is enabled.")
            if self.x_oauth_client_secret is None:
                raise ValueError("X_OAUTH_CLIENT_SECRET is required when X OAuth is enabled.")
            if self.oauth_transaction_encryption_key is None:
                raise ValueError(
                    "OAUTH_TRANSACTION_ENCRYPTION_KEY is required when X OAuth is enabled."
                )
            if self.integration_credentials_encryption_key is None:
                raise ValueError(
                    "INTEGRATION_CREDENTIALS_ENCRYPTION_KEY is required when X OAuth is enabled."
                )
            if "users.read" not in self.x_oauth_scopes:
                raise ValueError("X_OAUTH_SCOPES must include users.read.")
        if self.x_publishing_enabled and self.integration_credentials_encryption_key is None:
            raise ValueError(
                "INTEGRATION_CREDENTIALS_ENCRYPTION_KEY is required when X publishing is enabled."
            )
        if self.google_search_enabled:
            if not self.google_search_client_id:
                raise ValueError(
                    "GOOGLE_SEARCH_CLIENT_ID is required when Google Search is enabled."
                )
            if self.google_search_client_secret is None:
                raise ValueError(
                    "GOOGLE_SEARCH_CLIENT_SECRET is required when Google Search is enabled."
                )
            if self.oauth_transaction_encryption_key is None:
                raise ValueError(
                    "OAUTH_TRANSACTION_ENCRYPTION_KEY is required when Google Search is enabled."
                )
            if self.integration_credentials_encryption_key is None:
                raise ValueError(
                    "INTEGRATION_CREDENTIALS_ENCRYPTION_KEY is required when Google Search "
                    "is enabled."
                )
            if not self.inngest_enabled:
                raise ValueError("INNGEST_ENABLED=true is required when Google Search is enabled.")
        if (self.github_app_id is None) != (self.github_app_private_key is None):
            raise ValueError(
                "GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY must be configured together."
            )
        if self.github_app_id is not None and self.github_app_id < 1:
            raise ValueError("GITHUB_APP_ID must be positive.")
        if self.github_webhook_enabled:
            if self.github_webhook_secret is None:
                raise ValueError(
                    "GITHUB_WEBHOOK_SECRET is required when the GitHub webhook is enabled."
                )
            if self.github_webhook_payload_encryption_key is None:
                raise ValueError(
                    "GITHUB_WEBHOOK_PAYLOAD_ENCRYPTION_KEY is required when the GitHub webhook "
                    "is enabled."
                )
            if not self.inngest_enabled:
                raise ValueError(
                    "INNGEST_ENABLED=true is required when the GitHub webhook is enabled."
                )
        if self.inngest_enabled:
            if self.inngest_event_key is None:
                raise ValueError("INNGEST_EVENT_KEY is required when Inngest is enabled.")
            if self.app_env == "production" and self.inngest_signing_key is None:
                raise ValueError("INNGEST_SIGNING_KEY is required for production Inngest.")
        if self.github_webhook_enabled and self.github_webhook_payload_encryption_key is None:
            raise ValueError(
                "GITHUB_WEBHOOK_PAYLOAD_ENCRYPTION_KEY is required when the GitHub webhook "
                "is enabled."
            )
        if self.integration_credentials_previous_encryption_keys:
            if self.integration_credentials_encryption_key is None:
                raise ValueError(
                    "INTEGRATION_CREDENTIALS_ENCRYPTION_KEY is required when previous credential "
                    "keys are configured."
                )
            if (
                self.integration_credentials_active_key_version
                in self.integration_credentials_previous_encryption_keys
            ):
                raise ValueError(
                    "The active integration credential key version must not also be previous."
                )
        if self.generation_provider == "openai" and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when GENERATION_PROVIDER=openai.")
        if self.inngest_event_api_base_url is not None:
            parsed_endpoint = urlsplit(self.inngest_event_api_base_url)
            if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
                raise ValueError("INNGEST_EVENT_API_BASE_URL must be an absolute HTTP(S) origin.")
            if (
                parsed_endpoint.path not in {"", "/"}
                or parsed_endpoint.query
                or parsed_endpoint.fragment
            ):
                raise ValueError("INNGEST_EVENT_API_BASE_URL must not contain a path or query.")
        return self

    @field_validator(
        "jobs_queue_name",
        "jobs_email_queue",
        "jobs_files_queue",
        "jobs_ai_queue",
        "jobs_reports_queue",
        "jobs_webhooks_queue",
        "jobs_notifications_queue",
    )
    @classmethod
    def validate_queue_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Job queue names must not be empty.")
        return normalized

    @model_validator(mode="after")
    def validate_job_settings(self) -> "Settings":
        if self.jobs_backend == "rq" and not self.resolved_jobs_redis_url:
            raise ValueError("JOBS_REDIS_URL or REDIS_URL is required when JOBS_BACKEND=rq.")
        return self

    @field_validator("storage_public_base_url")
    @classmethod
    def validate_storage_public_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError("STORAGE_PUBLIC_BASE_URL must be an absolute HTTP(S) URL.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_email_settings(self) -> "Settings":
        if self.email_provider != "smtp":
            return self
        if not self.smtp_host:
            raise ValueError("SMTP_HOST is required when EMAIL_PROVIDER=smtp.")
        if not self.email_from:
            raise ValueError("EMAIL_FROM is required when EMAIL_PROVIDER=smtp.")
        if (self.smtp_user and not self.smtp_password) or (
            self.smtp_password and not self.smtp_user
        ):
            raise ValueError("SMTP_USER and SMTP_PASSWORD must be configured together.")
        if self.smtp_use_tls and self.smtp_use_ssl:
            raise ValueError("SMTP_USE_TLS and SMTP_USE_SSL cannot both be enabled.")
        return self

    @model_validator(mode="after")
    def validate_storage_settings(self) -> "Settings":
        if self.storage_provider == "local":
            if self.app_env == "production" and not self.storage_local_path:
                raise ValueError(
                    "STORAGE_LOCAL_PATH must be explicitly configured in production "
                    "when STORAGE_PROVIDER=local."
                )
            return self

        if not self.storage_bucket:
            raise ValueError("STORAGE_BUCKET is required when STORAGE_PROVIDER=s3.")
        if not self.storage_access_key:
            raise ValueError("STORAGE_ACCESS_KEY is required when STORAGE_PROVIDER=s3.")
        if not self.storage_secret_key:
            raise ValueError("STORAGE_SECRET_KEY is required when STORAGE_PROVIDER=s3.")
        return self

    @field_validator("cors_allow_credentials")
    @classmethod
    def prevent_wildcard_credentials(cls, value: bool, info: ValidationInfo) -> bool:
        origins = info.data.get("cors_origins", [])
        if value and "*" in [str(origin) for origin in origins]:
            msg = "CORS credentials cannot be enabled when cors_origins contains '*'."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.app_env != "production":
            return self

        if self.debug:
            raise ValueError("DEBUG must be false in production.")
        if not self.public_backend_url.startswith("https://"):
            raise ValueError("PUBLIC_BACKEND_URL must use HTTPS in production.")
        if "*" in self.allowed_hosts:
            raise ValueError("ALLOWED_HOSTS cannot contain '*' in production.")
        if self.proxy_headers and self.forwarded_allow_ips.strip() == "*":
            raise ValueError("FORWARDED_ALLOW_IPS must be restricted in production.")
        if not self.cors_origin_strings:
            raise ValueError("CORS_ORIGINS must be configured in production.")
        if self.jwt_secret_key == "change-me":
            raise ValueError("JWT_SECRET_KEY must be changed in production.")
        if self.oauth_enabled_providers or self.google_search_enabled:
            if not self.browser_cookie_secure:
                raise ValueError("BROWSER_COOKIE_SECURE must be true for production OAuth.")
            if self.browser_cookie_samesite != "lax":
                raise ValueError("BROWSER_COOKIE_SAMESITE must be lax for production OAuth.")
            if not self.browser_cookie_domain:
                raise ValueError("BROWSER_COOKIE_DOMAIN is required for production OAuth.")
            if not self.browser_allowed_origins:
                raise ValueError("BROWSER_ALLOWED_ORIGINS must contain exact production origins.")
        if self.email_provider == "smtp" and self.jobs_backend == "inline":
            raise ValueError("JOBS_BACKEND=rq is required for SMTP email in production.")
        if self.rate_limit_enabled and self.cache_backend == "memory" and self.workers > 1:
            raise ValueError(
                "CACHE_BACKEND=redis is required for rate limiting with multiple workers."
            )
        return self

    @property
    def cors_origin_strings(self) -> list[str]:
        return [origin.rstrip("/") if origin != "*" else origin for origin in self.cors_origins]

    @property
    def openapi_url(self) -> str | None:
        enabled = (
            self.openapi_enabled
            if self.openapi_enabled is not None
            else self.app_env != "production"
        )
        return "/openapi.json" if enabled else None

    @property
    def docs_url(self) -> str | None:
        enabled = (
            self.docs_enabled if self.docs_enabled is not None else self.app_env != "production"
        )
        return "/docs" if enabled else None

    @property
    def redoc_url(self) -> str | None:
        enabled = (
            self.redoc_enabled if self.redoc_enabled is not None else self.app_env != "production"
        )
        return "/redoc" if enabled else None

    @property
    def hsts_is_enabled(self) -> bool:
        return self.hsts_enabled if self.hsts_enabled is not None else self.app_env == "production"

    @property
    def expose_errors(self) -> bool:
        return (
            self.expose_error_details
            if self.expose_error_details is not None
            else self.app_env != "production"
        )

    @property
    def resolved_storage_local_path(self) -> str:
        return self.storage_local_path or "storage"

    @property
    def sentry_is_enabled(self) -> bool:
        return bool(self.sentry_dsn and self.sentry_dsn.strip())

    @property
    def resolved_jobs_redis_url(self) -> str | None:
        return self.jobs_redis_url or self.redis_url

    @property
    def resolved_browser_allowed_origins(self) -> list[str]:
        return self.browser_allowed_origins or [self.frontend_url]

    @property
    def integration_credentials_key_ring(self) -> dict[str, str]:
        keys = {
            version: secret.get_secret_value()
            for version, secret in self.integration_credentials_previous_encryption_keys.items()
        }
        if self.integration_credentials_encryption_key is not None:
            keys[self.integration_credentials_active_key_version] = (
                self.integration_credentials_encryption_key.get_secret_value()
            )
        return keys


@lru_cache
def get_settings() -> Settings:
    return Settings()
