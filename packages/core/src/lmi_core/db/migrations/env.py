"""Alembic migration environment for the LMI database.

Alembic resolves this file from the ``script_location`` set by
:func:`lmi_core.db.alembic_support.alembic_config`, which is the only entry
point into it. Nothing here reads the process environment or a file in the
working directory: the URL is already resolved from the application settings.
"""

from alembic import context
from lmi_core.db import models  # noqa: F401  (registers the tables on Base.metadata)
from lmi_core.db.base import Base
from sqlalchemy import create_engine, pool

# The Config that alembic_support.alembic_config built, with this directory as
# the script location and the URL derived from the settings.
config = context.config

# Autogenerate compares the live database against this metadata.
target_metadata = Base.metadata


def _database_url() -> str:
    """Return the configured URL, or stop instead of migrating a wrong database."""
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "alembic config has no sqlalchemy.url: build it with "
            "lmi_core.db.alembic_support.alembic_config"
        )
    return url


def run_migrations_offline() -> None:
    """Render the migrations as SQL instead of executing them."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migrations against the configured database."""
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
