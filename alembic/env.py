"""Alembic environment configuration.

Reads database URL from the application's settings module
and uses the ORM models' metadata for autogenerate support.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add project src to path so we can import our models
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv()

from pension_chat.db.models import Base

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the target metadata for autogenerate
target_metadata = Base.metadata

# Override sqlalchemy.url from environment
import urllib.parse
section = config.config_ini_section
config.set_section_option(section, "PGUSER", os.environ.get("PGUSER", ""))
pwd = os.environ.get("PGPASSWORD", "")
pwd = pwd.strip('"').strip("'")  # Remove quotes if they exist in env
pwd = urllib.parse.quote_plus(pwd).replace("%", "%%")
config.set_section_option(section, "PGPASSWORD", pwd)
config.set_section_option(section, "PGHOST", os.environ.get("PGHOST", ""))
config.set_section_option(section, "PGPORT", os.environ.get("PGPORT", "5432"))
config.set_section_option(section, "PGDATABASE", os.environ.get("PGDATABASE", "pension_chat_db"))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL script."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
