from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.infrastructure.database import Base
from app.infrastructure import models  # noqa: F401
from app.infrastructure.settings import Settings

config = context.config
config.set_main_option("sqlalchemy.url", Settings().DATABASE_URL)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


offline() if context.is_offline_mode() else online()
