"""Database-first primitives for idempotent inserts and duplicate replay."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class InsertReplayResult[T]:
    value: T
    inserted: bool


async def insert_or_replay(
    db: AsyncSession,
    table: Table,
    *,
    values: dict[str, Any],
    conflict_columns: tuple[str, ...],
) -> InsertReplayResult[RowMapping]:
    """Atomically insert a row or return the row owning the unique key."""
    if not conflict_columns or any(column not in values for column in conflict_columns):
        raise ValueError("conflict_columns must be non-empty and present in values")

    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        inserted = await db.execute(
            postgresql_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=list(conflict_columns))
            .returning(*table.c)
        )
    elif bind.dialect.name == "sqlite":
        inserted = await db.execute(
            sqlite_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=list(conflict_columns))
            .returning(*table.c)
        )
    else:
        raise NotImplementedError(f"idempotent inserts are unsupported for {bind.dialect.name}")
    row = inserted.mappings().one_or_none()
    if row is not None:
        return InsertReplayResult(value=row, inserted=True)

    replay = await db.execute(
        select(table).where(*(table.c[column] == values[column] for column in conflict_columns))
    )
    return InsertReplayResult(value=replay.mappings().one(), inserted=False)
