"""Programmatic Alembic wiring for the LMI database.

There is no ``alembic.ini``. Callers get a :class:`alembic.config.Config` whose
script location points at the migrations packaged next to this module, resolved
from ``__file__`` so it does not depend on the process working directory.
"""

from pathlib import Path

import alembic.command
import alembic.config

from lmi_core.config import Settings, sqlalchemy_url

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(settings: Settings) -> alembic.config.Config:
    """Return an Alembic config for the migration scripts in this package.

    The URL is rendered with the password visible, because Alembic needs it to
    connect, and every ``%`` is doubled: Alembic reads the option through a
    config parser with interpolation, and a percent-encoded password such as
    ``p%40ss`` would otherwise be read as an interpolation escape.
    """
    config = alembic.config.Config()
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    config.set_main_option(
        "sqlalchemy.url",
        sqlalchemy_url(settings).render_as_string(hide_password=False).replace("%", "%%"),
    )
    return config


def upgrade_to_head(settings: Settings) -> None:
    """Upgrade the configured database to the latest revision.

    Raises:
        alembic.util.exc.CommandError: the migration chain or the script
            directory is invalid.
        sqlalchemy.exc.SQLAlchemyError: the database cannot be reached or a
            migration statement fails.
    """
    alembic.command.upgrade(alembic_config(settings), "head")
