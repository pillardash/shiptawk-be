from fastapi import HTTPException
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.core.config import get_settings
from app.main import create_app


def test_request_id_header_is_preserved() -> None:
    client = TestClient(create_app())

    response = client.get("/health", headers={"X-Request-ID": "request-123"})

    assert response.headers["X-Request-ID"] == "request-123"


def test_process_time_header_is_added() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert "X-Process-Time" in response.headers


def test_security_headers_are_added() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == "geolocation=(), microphone=(), camera=()"


def test_body_size_limit_rejects_large_requests(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "10")
    get_settings.cache_clear()
    client = TestClient(create_app())

    response = client.post("/v1/auth/login", content="x" * 11)

    assert response.status_code == 413
    assert response.json()["code"] == "request_body_too_large"
    assert "requestId" in response.json()


def test_rate_limit_rejects_excess_requests(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()
    client = TestClient(create_app())

    assert client.get("/health").status_code == 200
    response = client.get("/health")

    assert response.status_code == 429
    assert response.headers["Retry-After"]
    assert response.json()["code"] == "rate_limited"


def test_rate_limit_does_not_trust_forwarded_for_header(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()
    client = TestClient(create_app())

    assert client.get("/health", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 200
    response = client.get("/health", headers={"X-Forwarded-For": "203.0.113.11"})

    assert response.status_code == 429


def test_rate_limit_trusts_forwarded_for_from_trusted_proxy(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "testclient")
    get_settings.cache_clear()
    client = TestClient(create_app())

    assert client.get("/health", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 200
    assert client.get("/health", headers={"X-Forwarded-For": "203.0.113.11"}).status_code == 200


def test_openapi_docs_are_disabled_in_production_by_default(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BACKEND_URL", "https://api.example.com")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("JWT_SECRET_KEY", "changed")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", "/tmp/app-storage")
    get_settings.cache_clear()
    client = TestClient(create_app())

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_production_masks_server_http_exception_details(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BACKEND_URL", "https://api.example.com")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("JWT_SECRET_KEY", "changed")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", "/tmp/app-storage")
    get_settings.cache_clear()
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise HTTPException(status_code=500, detail="database password leaked")

    client = TestClient(app)

    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error."


def test_app_exposes_job_service() -> None:
    app = create_app()

    assert hasattr(app.state, "jobs")
