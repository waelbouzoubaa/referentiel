from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from middleware.core.config import get_settings

# Objet de configuration Alembic
config = context.config

# Configuration du logger stdlib depuis alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Surcharge de l'URL depuis les Settings (priorité sur alembic.ini)
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

from middleware.db.base import Base
import middleware.db.models  # noqa: F401 — enregistre tous les modèles
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Exécute les migrations en mode offline (sans connexion active)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)  # type: ignore[arg-type]
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Exécute les migrations en mode async (connexion active asyncpg)."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Point d'entrée pour les migrations online."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
