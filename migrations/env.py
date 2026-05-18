import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

from alembic import context
from webhook_inspector.config import Settings
from webhook_inspector.infrastructure.database import models  # noqa: F401  - register tables

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Only fall back to Settings.database_url when no URL was injected at the
# call site (e.g. tests/conftest.py sets the testcontainer URL via
# `cfg.set_main_option(...)` before invoking `command.upgrade`).
_injected_url = config.get_main_option("sqlalchemy.url")
if not _injected_url or _injected_url.startswith("driver://"):
    settings = Settings()
    config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
