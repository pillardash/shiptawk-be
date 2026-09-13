import os
from collections.abc import Generator

import pytest
from pytest import MonkeyPatch

from app.core.config import Settings, get_settings
from app.db.session import reset_database_state

# Test collection imports the application before fixtures run. Disable dotenv
# loading process-wide so developer credentials cannot affect test behavior.
Settings.model_config["env_file"] = None


def pytest_sessionfinish(session: pytest.Session) -> None:
    if os.getenv("REQUIRE_NO_SKIPS") != "1":
        return
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = terminal.stats.get("skipped", []) if terminal is not None else []
    if skipped and terminal is not None:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        terminal.write_sep(
            "=", f"CI requires all tests to run; {len(skipped)} test(s) skipped", red=True
        )


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("APP_NAME", "Backend API")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/app")
    monkeypatch.setenv(
        "OAUTH_TRANSACTION_ENCRYPTION_KEY",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    monkeypatch.setenv("GITHUB_WEBHOOK_ENABLED", "false")
    reset_database_state()
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_database_state()
