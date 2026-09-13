from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def database_url(database: str) -> str:
    from app.core.config import get_settings

    parsed = urlsplit(get_settings().database_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))


def recreate_database(database: str) -> None:
    admin_url = database_url("postgres").replace("postgresql+psycopg", "postgresql", 1)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{database}"')


def upgrade(url: str, revision: str) -> None:
    from app.core.config import get_settings

    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, revision)


def main() -> None:
    from app.core.config import get_settings

    original_url = get_settings().database_url
    config = Config("alembic.ini")
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("Alembic has no migration head")
    parent = ScriptDirectory.from_config(config).get_revision(head).down_revision
    if not isinstance(parent, str):
        raise RuntimeError("Alembic migration history must have one linear parent")

    fresh_database = "app_migration_fresh"
    upgrade_database = "app_migration_upgrade"
    fresh_url = database_url(fresh_database)
    upgrade_url = database_url(upgrade_database)
    for database in (fresh_database, upgrade_database):
        recreate_database(database)

    upgrade(fresh_url, "head")
    upgrade(upgrade_url, parent)
    upgrade(upgrade_url, "head")
    upgrade(original_url, "head")


if __name__ == "__main__":
    main()
