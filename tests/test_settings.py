import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from app.core.config import Settings


def test_cors_origins_accept_comma_separated_env_value() -> None:
    settings = Settings(cors_origins="http://localhost:3000,http://localhost:5173")

    assert settings.cors_origin_strings == [
        "http://localhost:3000",
        "http://localhost:5173",
    ]


def test_cors_credentials_reject_wildcard_origin() -> None:
    with pytest.raises(ValidationError):
        Settings(cors_origins=["*"], cors_allow_credentials=True)


def test_settings_use_unprefixed_env_names(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Test API")
    monkeypatch.setenv("API_PREFIX", "/api/test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/test")

    settings = Settings()

    assert settings.app_name == "Test API"
    assert settings.api_v1_prefix == "/api/test"
    assert settings.database_url == "postgresql+psycopg://postgres:postgres@localhost:5432/test"


def test_log_level_accepts_lowercase_value() -> None:
    settings = Settings(log_level="debug")

    assert settings.log_level == "DEBUG"


def test_log_format_accepts_uppercase_value() -> None:
    settings = Settings(log_format="JSON")

    assert settings.log_format == "json"


def test_api_prefix_must_start_with_slash() -> None:
    with pytest.raises(ValidationError):
        Settings(API_PREFIX="api/v1")


def test_port_must_be_valid() -> None:
    with pytest.raises(ValidationError):
        Settings(port=70000)


def test_workers_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(workers=0)


def test_production_rejects_debug() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            debug=True,
            allowed_hosts=["api.example.com"],
        )


def test_production_rejects_wildcard_allowed_hosts() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", allowed_hosts=["*"])


def test_openapi_docs_disabled_by_default_in_production() -> None:
    settings = Settings(
        app_env="production",
        allowed_hosts=["api.example.com"],
        cors_origins=["https://app.example.com"],
        jwt_secret_key="changed",
        storage_local_path="/var/lib/app/storage",
    )

    assert settings.openapi_url is None
    assert settings.docs_url is None
    assert settings.redoc_url is None


def test_openapi_can_be_explicitly_enabled_in_production() -> None:
    settings = Settings(
        app_env="production",
        allowed_hosts=["api.example.com"],
        cors_origins=["https://app.example.com"],
        jwt_secret_key="changed",
        openapi_enabled=True,
        storage_local_path="/var/lib/app/storage",
    )

    assert settings.openapi_url == "/openapi.json"


def test_redis_cache_backend_requires_redis_url() -> None:
    with pytest.raises(ValidationError):
        Settings(cache_backend="redis")


def test_production_smtp_requires_rq_jobs() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            allowed_hosts=["api.example.com"],
            cors_origins=["https://app.example.com"],
            jwt_secret_key="changed",
            storage_local_path="/var/lib/app/storage",
            email_provider="smtp",
            smtp_host="smtp.example.com",
            email_from="noreply@example.com",
        )


def test_smtp_tls_and_ssl_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            email_provider="smtp",
            smtp_host="smtp.example.com",
            email_from="noreply@example.com",
            smtp_use_tls=True,
            smtp_use_ssl=True,
        )


def test_production_multi_worker_rate_limit_requires_redis_cache() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            allowed_hosts=["api.example.com"],
            cors_origins=["https://app.example.com"],
            jwt_secret_key="changed",
            storage_local_path="/var/lib/app/storage",
            workers=2,
            rate_limit_enabled=True,
            cache_backend="memory",
        )
