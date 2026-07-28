from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.idempotency import insert_or_replay


@pytest.fixture
async def db_and_table() -> AsyncGenerator[tuple[AsyncSession, Table]]:
    metadata = MetaData()
    table = Table(
        "idempotency_examples",
        metadata,
        Column("id", String, primary_key=True),
        Column("scope", String, nullable=False),
        Column("key", String, nullable=False),
        Column("value", String, nullable=False),
        __import__("sqlalchemy").UniqueConstraint("scope", "key"),
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        yield db, table
    await engine.dispose()


async def test_insert_or_replay_returns_atomic_insert_then_existing_row(
    db_and_table: tuple[AsyncSession, Table],
) -> None:
    db, table = db_and_table
    first_id = str(uuid4())

    first = await insert_or_replay(
        db,
        table,
        values={"id": first_id, "scope": "tenant-a", "key": "same", "value": "first"},
        conflict_columns=("scope", "key"),
    )
    replay = await insert_or_replay(
        db,
        table,
        values={"id": str(uuid4()), "scope": "tenant-a", "key": "same", "value": "second"},
        conflict_columns=("scope", "key"),
    )

    assert first.inserted is True
    assert first.value["id"] == first_id
    assert replay.inserted is False
    assert replay.value["id"] == first_id
    assert replay.value["value"] == "first"


async def test_insert_or_replay_keeps_tenant_scopes_independent(
    db_and_table: tuple[AsyncSession, Table],
) -> None:
    db, table = db_and_table
    first = await insert_or_replay(
        db,
        table,
        values={"id": "a", "scope": "tenant-a", "key": "same", "value": "a"},
        conflict_columns=("scope", "key"),
    )
    second = await insert_or_replay(
        db,
        table,
        values={"id": "b", "scope": "tenant-b", "key": "same", "value": "b"},
        conflict_columns=("scope", "key"),
    )

    assert first.inserted is True
    assert second.inserted is True
    assert second.value["id"] == "b"
