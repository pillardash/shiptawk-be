from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.db.base import Base
from app.db.session import get_db
from app.domains.auth.models import AuthSession, AuthToken, AuthTokenPurpose
from app.domains.auth.service import create_auth_token
from app.domains.users.repository import get_user_by_email
from app.main import create_app
from app.services.jobs.base import JobHandle


class FakeJobService:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, object]]] = []
        self.should_fail = False

    def enqueue(
        self,
        task_name: str,
        *,
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        queue: str | None = None,
        delay_seconds: int | None = None,
    ) -> JobHandle:
        if self.should_fail:
            raise RuntimeError("queue unavailable")
        self.jobs.append((task_name, kwargs or {}))
        return JobHandle(id="job-id", task_name=task_name, queue=queue or "default")


@pytest.fixture
def auth_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def test_password_hashing_verifies_and_does_not_store_plain_password() -> None:
    hashed_password = hash_password("password123")

    assert hashed_password != "password123"
    assert verify_password("password123", hashed_password)


def test_access_token_round_trip() -> None:
    token = create_access_token("subject")

    assert decode_access_token(token) == "subject"


def test_register_returns_user_and_token_with_camel_case_fields(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/register",
        json={"email": "USER@example.com", "password": "password123"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["accessToken"]
    assert payload["refreshToken"]
    assert payload["tokenType"] == "bearer"
    assert payload["user"]["email"] == "user@example.com"
    assert payload["user"]["isActive"] is True
    assert payload["user"]["isVerified"] is False
    assert "passwordHash" not in payload["user"]
    assert "password_hash" not in payload["user"]


def test_register_stores_session_metadata(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/register",
        headers={"User-Agent": "pytest-agent"},
        json={
            "email": "user@example.com",
            "password": "password123",
            "deviceName": "Firefox on Linux",
        },
    )
    assert response.status_code == 201

    app = auth_client.app
    assert isinstance(app, FastAPI)
    db = next(app.dependency_overrides[get_db]())
    session = db.query(AuthSession).first()
    assert session is not None
    assert session.user_agent == "pytest-agent"
    assert session.ip_address == "testclient"
    assert session.device_name == "Firefox on Linux"
    db.close()


def test_register_truncates_session_metadata(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/register",
        headers={"User-Agent": "a" * 600},
        json={
            "email": "user@example.com",
            "password": "password123",
            "deviceName": "d" * 200,
        },
    )
    assert response.status_code == 201

    app = auth_client.app
    assert isinstance(app, FastAPI)
    db = next(app.dependency_overrides[get_db]())
    session = db.query(AuthSession).first()
    assert session is not None
    assert len(session.user_agent or "") == 512
    assert len(session.device_name or "") == 120
    db.close()


def test_register_duplicate_email_returns_conflict(auth_client: TestClient) -> None:
    body = {"email": "user@example.com", "password": "password123"}

    assert auth_client.post("/api/v1/auth/register", json=body).status_code == 201
    response = auth_client.post("/api/v1/auth/register", json=body)

    assert response.status_code == 409
    assert response.json()["code"] == "user_exists"
    assert "requestId" in response.json()


def test_login_returns_token_for_valid_credentials(auth_client: TestClient) -> None:
    body = {"email": "user@example.com", "password": "password123"}
    auth_client.post("/api/v1/auth/register", json=body)

    response = auth_client.post("/api/v1/auth/login", json=body)

    assert response.status_code == 200
    assert response.json()["accessToken"]
    assert response.json()["refreshToken"]


def test_refresh_rotates_refresh_token(auth_client: TestClient) -> None:
    body = {"email": "user@example.com", "password": "password123"}
    register_response = auth_client.post("/api/v1/auth/register", json=body)
    refresh_token = register_response.json()["refreshToken"]

    response = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"User-Agent": "rotated-agent"},
        json={"refreshToken": refresh_token, "deviceName": "Mobile App"},
    )

    assert response.status_code == 200
    assert response.json()["accessToken"]
    assert response.json()["refreshToken"] != refresh_token
    app = auth_client.app
    assert isinstance(app, FastAPI)
    db = next(app.dependency_overrides[get_db]())
    active_session = (
        db.query(AuthSession)
        .filter(AuthSession.revoked_at.is_(None), AuthSession.last_used_at.is_(None))
        .first()
    )
    assert active_session is not None
    assert active_session.user_agent == "rotated-agent"
    assert active_session.device_name == "Mobile App"
    db.close()
    reused_response = auth_client.post("/api/v1/auth/refresh", json={"refreshToken": refresh_token})
    assert reused_response.status_code == 401


def test_logout_revokes_refresh_token(auth_client: TestClient) -> None:
    register_response = auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )
    refresh_token = register_response.json()["refreshToken"]

    response = auth_client.post("/api/v1/auth/logout", json={"refreshToken": refresh_token})

    assert response.status_code == 200
    refresh_response = auth_client.post(
        "/api/v1/auth/refresh", json={"refreshToken": refresh_token}
    )
    assert refresh_response.status_code == 401


def test_login_rejects_invalid_credentials(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "password123"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_login_throttles_by_email(auth_client: TestClient, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("LOGIN_THROTTLE_EMAIL_REQUESTS", "1")
    monkeypatch.setenv("LOGIN_THROTTLE_IP_REQUESTS", "100")
    from app.core.config import get_settings

    get_settings.cache_clear()

    body = {"email": "missing@example.com", "password": "password123"}
    assert auth_client.post("/api/v1/auth/login", json=body).status_code == 401
    response = auth_client.post("/api/v1/auth/login", json=body)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_login_throttle_does_not_count_successes(
    auth_client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("LOGIN_THROTTLE_EMAIL_REQUESTS", "1")
    monkeypatch.setenv("LOGIN_THROTTLE_IP_REQUESTS", "100")
    from app.core.config import get_settings

    get_settings.cache_clear()
    body = {"email": "user@example.com", "password": "password123"}
    assert auth_client.post("/api/v1/auth/register", json=body).status_code == 201

    assert auth_client.post("/api/v1/auth/login", json=body).status_code == 200
    assert auth_client.post("/api/v1/auth/login", json=body).status_code == 200


def test_password_policy_requires_digit(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password"},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "password"


def test_me_returns_current_user(auth_client: TestClient) -> None:
    register_response = auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )
    token = register_response.json()["accessToken"]

    response = auth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"


def test_me_requires_token(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["code"] == "missing_token"
    assert "requestId" in response.json()


def test_change_password_updates_password_and_revokes_sessions(auth_client: TestClient) -> None:
    register_response = auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )
    access_token = register_response.json()["accessToken"]
    refresh_token = register_response.json()["refreshToken"]

    response = auth_client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"currentPassword": "password123", "newPassword": "new-password123"},
    )

    assert response.status_code == 200
    assert (
        auth_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "password123"},
        ).status_code
        == 401
    )
    assert (
        auth_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "new-password123"},
        ).status_code
        == 200
    )
    assert (
        auth_client.post("/api/v1/auth/refresh", json={"refreshToken": refresh_token}).status_code
        == 401
    )


def test_me_rejects_invalid_token(auth_client: TestClient) -> None:
    response = auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_login_rejects_inactive_user(auth_client: TestClient) -> None:
    auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )

    app = auth_client.app
    assert isinstance(app, FastAPI)
    override = app.dependency_overrides[get_db]
    db = next(override())
    user = get_user_by_email(db, "user@example.com")
    assert user is not None
    user.is_active = False
    db.commit()
    db.close()

    response = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "password123"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "inactive_user"


def test_repository_filters_soft_deleted_users(auth_client: TestClient) -> None:
    auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )

    app = auth_client.app
    assert isinstance(app, FastAPI)
    override = app.dependency_overrides[get_db]
    db = next(override())
    user = get_user_by_email(db, "user@example.com")
    assert user is not None
    user.deleted_at = user.created_at
    db.commit()

    assert get_user_by_email(db, "user@example.com") is None
    db.close()


def test_forgot_password_is_enumeration_safe(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/auth/forgot-password", json={"email": "none@example.com"})

    assert response.status_code == 200
    assert response.json() == {
        "message": "If an account exists, password reset instructions will be sent."
    }


def test_forgot_password_enqueues_reset_email_for_existing_user(auth_client: TestClient) -> None:
    auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )
    jobs = FakeJobService()
    app = auth_client.app
    assert isinstance(app, FastAPI)
    app.state.jobs = jobs

    response = auth_client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})

    assert response.status_code == 200
    assert len(jobs.jobs) == 1
    task_name, kwargs = jobs.jobs[0]
    assert task_name == "email.send_password_reset"
    assert kwargs["recipient_email"] == "user@example.com"
    assert str(kwargs["reset_url"]).startswith("http://localhost:3000/reset-password?token=")


def test_forgot_password_does_not_send_email_for_missing_user(auth_client: TestClient) -> None:
    jobs = FakeJobService()
    app = auth_client.app
    assert isinstance(app, FastAPI)
    app.state.jobs = jobs

    response = auth_client.post(
        "/api/v1/auth/forgot-password", json={"email": "missing@example.com"}
    )

    assert response.status_code == 200
    assert jobs.jobs == []


def test_forgot_password_hides_job_enqueue_failures(auth_client: TestClient) -> None:
    auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )
    jobs = FakeJobService()
    jobs.should_fail = True
    app = auth_client.app
    assert isinstance(app, FastAPI)
    app.state.jobs = jobs

    response = auth_client.post("/api/v1/auth/forgot-password", json={"email": "user@example.com"})

    assert response.status_code == 200
    assert response.json() == {
        "message": "If an account exists, password reset instructions will be sent."
    }


def test_email_verification_request_enqueues_email_for_existing_user(
    auth_client: TestClient,
) -> None:
    auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )
    jobs = FakeJobService()
    app = auth_client.app
    assert isinstance(app, FastAPI)
    app.state.jobs = jobs

    response = auth_client.post(
        "/api/v1/auth/email-verification/request", json={"email": "user@example.com"}
    )

    assert response.status_code == 200
    assert len(jobs.jobs) == 1
    task_name, kwargs = jobs.jobs[0]
    assert task_name == "email.send_verification"
    assert kwargs["recipient_email"] == "user@example.com"
    assert str(kwargs["verification_url"]).startswith("http://localhost:3000/verify-email?token=")


def test_unsupported_auth_token_purpose_is_rejected(auth_client: TestClient) -> None:
    app = auth_client.app
    assert isinstance(app, FastAPI)
    db = next(app.dependency_overrides[get_db]())

    with pytest.raises(ValueError):
        create_auth_token(db, email="user@example.com", purpose=AuthTokenPurpose.magic_login)

    db.close()


def test_reset_password_token_works_once(auth_client: TestClient) -> None:
    auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )
    app = auth_client.app
    assert isinstance(app, FastAPI)
    db = next(app.dependency_overrides[get_db]())
    token = create_auth_token(db, email="user@example.com", purpose=AuthTokenPurpose.password_reset)
    assert token is not None
    stored_token = db.query(AuthToken).first()
    assert stored_token is not None
    assert stored_token.token_hash != token
    db.close()

    response = auth_client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "newPassword": "new-password123"},
    )

    assert response.status_code == 200
    assert (
        auth_client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "new-password123"},
        ).status_code
        == 200
    )
    assert (
        auth_client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "newPassword": "another-password123"},
        ).status_code
        == 401
    )


def test_reset_password_is_throttled(auth_client: TestClient, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PASSWORD_RESET_THROTTLE_TOKEN_REQUESTS", "1")
    monkeypatch.setenv("PASSWORD_RESET_THROTTLE_IP_REQUESTS", "100")
    from app.core.config import get_settings

    get_settings.cache_clear()
    body = {"token": "missing-token", "newPassword": "new-password123"}

    assert auth_client.post("/api/v1/auth/reset-password", json=body).status_code == 401
    response = auth_client.post("/api/v1/auth/reset-password", json=body)

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_email_verification_token_marks_user_verified(auth_client: TestClient) -> None:
    auth_client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )
    app = auth_client.app
    assert isinstance(app, FastAPI)
    db = next(app.dependency_overrides[get_db]())
    token = create_auth_token(
        db, email="user@example.com", purpose=AuthTokenPurpose.email_verification
    )
    assert token is not None
    db.close()

    response = auth_client.post("/api/v1/auth/email-verification/verify", json={"token": token})

    assert response.status_code == 200
    assert response.json()["isVerified"] is True
