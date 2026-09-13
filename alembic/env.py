import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.db import outbox as outbox_models  # noqa: F401
from app.db.base import Base
from app.modules.achievement_digests import models as achievement_digest_models  # noqa: F401
from app.modules.analytics import models as analytics_models  # noqa: F401
from app.modules.drafts import models as draft_models  # noqa: F401
from app.modules.evidence import models as evidence_models  # noqa: F401
from app.modules.identity import models as identity_models  # noqa: F401
from app.modules.integrations import models as integration_models  # noqa: F401
from app.modules.llm import models as llm_models  # noqa: F401
from app.modules.llm.models import llm_execution as llm_execution_models  # noqa: F401
from app.modules.llm.models import llm_execution_attempt as llm_attempt_models  # noqa: F401
from app.modules.operator import models as operator_models  # noqa: F401
from app.modules.products import models as product_models  # noqa: F401
from app.modules.repos import models as repos_models  # noqa: F401
from app.modules.search_intelligence import models as search_intelligence_models  # noqa: F401
from app.modules.workspaces import models as workspace_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
