from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import app.api.v1.health as health_module
from app.main import create_app


def test_root_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "Backend API",
        "environment": "local",
        "version": "0.1.0",
    }


def test_versioned_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_endpoint(monkeypatch: MonkeyPatch) -> None:
    async def pass_database_check() -> None:
        return None

    monkeypatch.setattr(health_module, "check_database_ready", pass_database_check)
    client = TestClient(create_app())

    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_endpoint_returns_503_when_database_is_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    async def fail_database_check() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(health_module, "check_database_ready", fail_database_check)
    client = TestClient(create_app())

    response = client.get("/api/v1/ready", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Database is not ready.",
        "code": "database_unavailable",
        "requestId": "test-request",
    }
