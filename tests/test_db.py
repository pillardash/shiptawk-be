from typing import cast
from unittest.mock import AsyncMock, Mock

from pytest import MonkeyPatch
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Table

import app.db.session as session_module
from app.core.config import Settings
from app.db.base import Base, BaseModel
from app.db.health import check_database_ready
from app.db.session import get_db, session_scope


def test_base_metadata_uses_stable_naming_convention() -> None:
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"
    assert Base.metadata.naming_convention["fk"] == (
        "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    )


def test_base_model_provides_uuid_primary_key_and_timestamps() -> None:
    class ExampleModel(BaseModel):
        __tablename__ = "test_example_models"

        name: Mapped[str] = mapped_column()

    columns = ExampleModel.__table__.columns

    assert "id" in columns
    assert columns["id"].primary_key
    assert "created_at" in columns
    assert "updated_at" in columns
    assert "created_by" in columns
    assert columns["created_by"].nullable
    assert "updated_by" in columns
    assert columns["updated_by"].nullable
    assert "deleted_at" in columns
    assert columns["deleted_at"].nullable

    Base.metadata.remove(cast(Table, ExampleModel.__table__))


def test_engine_uses_configured_pool_settings(monkeypatch: MonkeyPatch) -> None:
    create_engine = Mock()
    settings = Settings(
        database_url="postgresql+psycopg://postgres:postgres@localhost:5432/test",
        database_pool_size=3,
        database_max_overflow=4,
        database_pool_timeout=5,
        database_pool_recycle=6,
        database_echo=True,
    )

    monkeypatch.setattr(session_module, "create_async_engine", create_engine)
    monkeypatch.setattr(session_module, "get_settings", lambda: settings)
    session_module.reset_database_state()

    session_module.get_engine()

    create_engine.assert_called_once_with(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=4,
        pool_timeout=5,
        pool_recycle=6,
        echo=True,
    )


async def test_get_db_closes_session(monkeypatch: MonkeyPatch) -> None:
    db = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = db
    monkeypatch.setattr(
        session_module, "get_sessionmaker", lambda: Mock(return_value=session_context)
    )

    dependency = get_db()
    assert await anext(dependency) is db
    await dependency.aclose()

    session_context.__aexit__.assert_awaited_once()


async def test_session_scope_commits_and_closes(monkeypatch: MonkeyPatch) -> None:
    db = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = db
    monkeypatch.setattr(
        session_module, "get_sessionmaker", lambda: Mock(return_value=session_context)
    )

    async with session_scope() as session:
        assert session is db

    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()
    session_context.__aexit__.assert_awaited_once()


async def test_session_scope_rolls_back_and_closes(monkeypatch: MonkeyPatch) -> None:
    db = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = db
    monkeypatch.setattr(
        session_module, "get_sessionmaker", lambda: Mock(return_value=session_context)
    )

    try:
        async with session_scope():
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once_with()
    session_context.__aexit__.assert_awaited_once()


async def test_database_readiness_executes_lightweight_query(monkeypatch: MonkeyPatch) -> None:
    db = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = db
    monkeypatch.setattr(
        "app.db.health.get_sessionmaker",
        lambda: Mock(return_value=session_context),
    )

    await check_database_ready()

    db.execute.assert_awaited_once()
