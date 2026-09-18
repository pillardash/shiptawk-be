import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.sentry import configure_sentry
from app.main import create_app


def test_sentry_disabled_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("app.core.sentry.sentry_sdk.init", lambda **kwargs: calls.append(kwargs))

    configure_sentry(Settings())

    assert calls == []


@pytest.mark.parametrize("dsn", ["", "   "])
def test_sentry_disabled_with_blank_dsn(dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("app.core.sentry.sentry_sdk.init", lambda **kwargs: calls.append(kwargs))

    configure_sentry(Settings(sentry_dsn=dsn))

    assert calls == []


def test_sentry_enabled_with_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("app.core.sentry.sentry_sdk.init", lambda **kwargs: calls.append(kwargs))

    configure_sentry(
        Settings(
            sentry_dsn="https://public@example.com/1",
            sentry_traces_sample_rate=0.25,
            sentry_profiles_sample_rate=0.1,
            sentry_send_default_pii=False,
        )
    )

    assert calls == [
        {
            "dsn": "https://public@example.com/1",
            "environment": "local",
            "release": "0.1.0",
            "traces_sample_rate": 0.25,
            "profiles_sample_rate": 0.1,
            "send_default_pii": False,
        }
    ]


def test_sentry_sample_rates_must_be_valid() -> None:
    with pytest.raises(ValueError):
        Settings(sentry_traces_sample_rate=1.1)

    with pytest.raises(ValueError):
        Settings(sentry_profiles_sample_rate=-0.1)


def test_request_id_is_attached_to_sentry_context(monkeypatch: pytest.MonkeyPatch) -> None:
    tags: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.core.sentry.sentry_sdk.set_tag", lambda key, value: tags.append((key, value))
    )

    client = TestClient(create_app())
    response = client.get("/health", headers={"X-Request-ID": "request-123"})

    assert response.status_code == 200
    assert ("request_id", "request-123") in tags


def test_unhandled_errors_are_captured_by_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Exception] = []
    monkeypatch.setattr(
        "app.core.exception_handlers.capture_exception",
        lambda exc: captured.append(exc),
    )
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    assert len(captured) == 1
    assert isinstance(captured[0], RuntimeError)


def test_expected_http_exceptions_are_not_captured(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Exception] = []
    monkeypatch.setattr(
        "app.core.exception_handlers.capture_exception",
        lambda exc: captured.append(exc),
    )
    app = create_app()

    @app.get("/expected")
    def expected() -> None:
        raise HTTPException(status_code=400, detail="expected")

    client = TestClient(app)

    response = client.get("/expected")
    assert response.status_code == 400
    assert captured == []


def test_create_app_configures_sentry_when_dsn_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.com/1")
    get_settings.cache_clear()
    monkeypatch.setattr("app.core.sentry.sentry_sdk.init", lambda **kwargs: calls.append(kwargs))

    create_app()

    assert calls[0]["dsn"] == "https://public@example.com/1"
