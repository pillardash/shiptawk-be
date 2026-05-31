from collections.abc import Generator

import pytest
from pytest import MonkeyPatch

from app.core.config import get_settings
from app.db.session import reset_database_state


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("APP_NAME", "Backend API")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/app")
    reset_database_state()
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    reset_database_state()
