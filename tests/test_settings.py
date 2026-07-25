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


def test_settings_ignore_empty_optional_environment_values(monkeypatch: MonkeyPatch) -> None:
    for name in (
        "TRUSTED_PROXY_IPS",
        "GITHUB_APP_ID",
        "BROWSER_COOKIE_DOMAIN",
        "OPENAPI_ENABLED",
        "EMAIL_PROVIDER",
        "EMAIL_FROM",
        "STORAGE_PUBLIC_BASE_URL",
    ):
        monkeypatch.setenv(name, "")

    settings = Settings(_env_file=None)

    assert settings.trusted_proxy_ips == []
    assert settings.github_app_id is None
    assert settings.browser_cookie_domain is None
    assert settings.openapi_enabled is None
    assert settings.email_provider is None
    assert settings.email_from is None
    assert settings.storage_public_base_url is None


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
        public_backend_url="https://api.example.com",
        forwarded_allow_ips="10.0.0.0/8",
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
        public_backend_url="https://api.example.com",
        forwarded_allow_ips="10.0.0.0/8",
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


def test_production_oauth_requires_transaction_encryption_key(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAUTH_TRANSACTION_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValidationError, match="OAUTH_TRANSACTION_ENCRYPTION_KEY"):
        Settings(
            app_env="production",
            allowed_hosts=["api.example.com"],
            cors_origins=["https://app.example.com"],
            jwt_secret_key="changed",
            storage_local_path="/var/lib/app/storage",
            oauth_enabled_providers=["google"],
        )


def test_oauth_transaction_encryption_key_must_be_fernet_compatible() -> None:
    with pytest.raises(ValidationError, match="Fernet key"):
        Settings(oauth_transaction_encryption_key="not-a-fernet-key")


def test_production_browser_oauth_requires_shiptawk_safe_cookie_configuration() -> None:
    with pytest.raises(ValidationError, match="BROWSER_COOKIE_SECURE"):
        Settings(
            app_env="production",
            public_backend_url="https://api.shiptawk.com",
            forwarded_allow_ips="10.0.0.0/8",
            allowed_hosts=["api.shiptawk.com"],
            cors_origins=["https://app.shiptawk.com"],
            browser_allowed_origins=["https://app.shiptawk.com"],
            browser_cookie_domain=".shiptawk.com",
            jwt_secret_key="changed",
            storage_local_path="/var/lib/app/storage",
            oauth_enabled_providers=["github"],
            oauth_transaction_encryption_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            github_oauth_client_id="client",
            github_oauth_client_secret="secret",
        )


def test_production_shiptawk_browser_oauth_configuration_is_valid() -> None:
    settings = Settings(
        app_env="production",
        public_backend_url="https://api.shiptawk.com",
        forwarded_allow_ips="10.0.0.0/8",
        frontend_url="https://app.shiptawk.com",
        allowed_hosts=["api.shiptawk.com"],
        cors_origins=["https://app.shiptawk.com"],
        browser_allowed_origins=["https://app.shiptawk.com"],
        browser_cookie_domain=".shiptawk.com",
        browser_cookie_secure=True,
        browser_cookie_samesite="lax",
        jwt_secret_key="changed",
        storage_local_path="/var/lib/app/storage",
        oauth_enabled_providers=["github"],
        oauth_transaction_encryption_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        github_oauth_client_id="client",
        github_oauth_client_secret="secret",
    )

    assert settings.resolved_browser_allowed_origins == ["https://app.shiptawk.com"]
    assert settings.browser_cookie_domain == ".shiptawk.com"


def test_production_rejects_unrestricted_forwarded_header_trust() -> None:
    with pytest.raises(ValidationError, match="FORWARDED_ALLOW_IPS"):
        Settings(
            app_env="production",
            public_backend_url="https://api.example.com",
            allowed_hosts=["api.example.com"],
            cors_origins=["https://app.example.com"],
            jwt_secret_key="changed",
            storage_local_path="/var/lib/app/storage",
        )


def test_production_requires_https_public_backend_url() -> None:
    with pytest.raises(ValidationError, match="PUBLIC_BACKEND_URL"):
        Settings(
            app_env="production",
            public_backend_url="http://api.shiptawk.com",
            allowed_hosts=["api.shiptawk.com"],
            cors_origins=["https://app.shiptawk.com"],
            jwt_secret_key="changed",
            storage_local_path="/var/lib/app/storage",
        )


def test_public_backend_url_must_be_canonical() -> None:
    with pytest.raises(ValidationError, match="PUBLIC_BACKEND_URL"):
        Settings(public_backend_url="https://api.example.com/base?query=unsafe")


def test_github_app_configuration_requires_both_credentials() -> None:
    with pytest.raises(ValidationError, match="GITHUB_APP_PRIVATE_KEY"):
        Settings(github_app_id=123)


def test_github_provider_requires_complete_configuration() -> None:
    with pytest.raises(ValidationError, match="GITHUB_OAUTH_CLIENT_SECRET"):
        Settings(oauth_enabled_providers=["github"], github_oauth_client_id="client")


def test_enabled_github_webhook_requires_secret_and_valid_encryption_key() -> None:
    with pytest.raises(ValidationError, match="GITHUB_WEBHOOK_SECRET"):
        Settings(github_webhook_enabled=True)

    with pytest.raises(ValidationError, match="GITHUB_WEBHOOK_SECRET"):
        Settings(
            github_webhook_enabled=True,
            github_webhook_secret="",
            github_webhook_payload_encryption_key=("MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="),
        )

    with pytest.raises(ValidationError, match="Fernet key"):
        Settings(
            github_webhook_enabled=True,
            github_webhook_secret="webhook-secret",
            github_webhook_payload_encryption_key="invalid",
        )


def test_enabled_github_webhook_requires_inngest_delivery() -> None:
    with pytest.raises(ValidationError, match="INNGEST_ENABLED"):
        Settings(
            github_webhook_enabled=True,
            github_webhook_secret="webhook-secret",
            github_webhook_payload_encryption_key=("iezSZZMKlqtExgXjvfgFPjnpXI7Vb9RWcYNnNr84jm8="),
        )


def test_production_rejects_incomplete_github_webhook_configuration() -> None:
    with pytest.raises(ValidationError, match="GITHUB_WEBHOOK_PAYLOAD_ENCRYPTION_KEY"):
        Settings(
            app_env="production",
            public_backend_url="https://api.shiptawk.com",
            allowed_hosts=["api.shiptawk.com"],
            cors_origins=["https://app.shiptawk.com"],
            jwt_secret_key="changed",
            storage_local_path="/var/lib/app/storage",
            forwarded_allow_ips="10.0.0.0/8",
            github_webhook_enabled=True,
            github_webhook_secret="webhook-secret",
        )


def test_inngest_workflows_are_disabled_by_default() -> None:
    settings = Settings()

    assert settings.inngest_enabled is False


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "INNGEST_EVENT_KEY"),
        ({"inngest_event_key": "event-key"}, "GITHUB_WEBHOOK_PAYLOAD_ENCRYPTION_KEY"),
        (
            {
                "inngest_event_key": "event-key",
                "github_webhook_payload_encryption_key": (
                    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
                ),
            },
            "GENERATION_PROVIDER",
        ),
    ],
)
def test_enabled_inngest_rejects_incomplete_configuration(
    overrides: dict[str, str], expected: str
) -> None:
    with pytest.raises(ValidationError, match=expected):
        Settings.model_validate({"inngest_enabled": True, **overrides})


def test_production_inngest_requires_signing_key() -> None:
    common = {
        "app_env": "production",
        "public_backend_url": "https://api.example.com",
        "allowed_hosts": ["api.example.com"],
        "cors_origins": ["https://app.example.com"],
        "jwt_secret_key": "changed",
        "storage_local_path": "/tmp/storage",
        "forwarded_allow_ips": "10.0.0.0/8",
        "inngest_enabled": True,
        "inngest_event_key": "event-key",
        "github_webhook_payload_encryption_key": ("MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="),
        "generation_provider": "openai",
        "openai_api_key": "openai-key",
    }
    with pytest.raises(ValidationError, match="INNGEST_SIGNING_KEY"):
        Settings.model_validate(common)
