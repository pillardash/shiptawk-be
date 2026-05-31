from functools import lru_cache
from typing import Annotated, Literal, cast

from pydantic import EmailStr, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "production"]
LogFormat = Literal["console", "json"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
EmailProvider = Literal["smtp"]
StorageProvider = Literal["local", "s3"]
JobsBackend = Literal["inline", "rq"]


class Settings(BaseSettings):
    app_name: str = "Backend API"
    app_version: str = "0.1.0"
    app_env: AppEnv = "local"
    debug: bool = False
    api_v1_prefix: str = Field(default="/api/v1", alias="API_PREFIX")
    frontend_url: str = "http://localhost:3000"
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
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    password_reset_token_expire_minutes: int = 30
    email_verification_token_expire_hours: int = 24
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
    login_throttle_enabled: bool = True
    login_throttle_ip_requests: int = 10
    login_throttle_email_requests: int = 5
    login_throttle_window_seconds: int = 300
    password_reset_throttle_ip_requests: int = 10
    password_reset_throttle_token_requests: int = 5
    password_reset_throttle_window_seconds: int = 300
    password_min_length: int = 8
    password_max_length: int = 128
    password_require_letter: bool = True
    password_require_digit: bool = True
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
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str] | str:
        if isinstance(value, str) and value:
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

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
        "login_throttle_ip_requests",
        "login_throttle_email_requests",
        "login_throttle_window_seconds",
        "password_reset_throttle_ip_requests",
        "password_reset_throttle_token_requests",
        "password_reset_throttle_window_seconds",
        "password_min_length",
        "password_max_length",
        "storage_presigned_url_expire_seconds",
        "jobs_default_timeout_seconds",
        "jobs_result_ttl_seconds",
        "jobs_failure_ttl_seconds",
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

    @model_validator(mode="after")
    def validate_password_length_settings(self) -> "Settings":
        if self.password_min_length > self.password_max_length:
            raise ValueError("PASSWORD_MIN_LENGTH cannot exceed PASSWORD_MAX_LENGTH.")
        if self.cache_backend == "redis" and not self.redis_url:
            raise ValueError("REDIS_URL is required when CACHE_BACKEND=redis.")
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
        if "*" in self.allowed_hosts:
            raise ValueError("ALLOWED_HOSTS cannot contain '*' in production.")
        if not self.cors_origin_strings:
            raise ValueError("CORS_ORIGINS must be configured in production.")
        if self.jwt_secret_key == "change-me":
            raise ValueError("JWT_SECRET_KEY must be changed in production.")
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
        return bool(self.sentry_dsn)

    @property
    def resolved_jobs_redis_url(self) -> str | None:
        return self.jobs_redis_url or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
