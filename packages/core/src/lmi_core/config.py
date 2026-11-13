"""Runtime configuration shared by the LMI services.

Every value is read from an environment variable. There is deliberately no
env-file support: the deployment environment provides the values, and no
credential has a default in this repository.

Values that can carry credentials are wrapped in :class:`pydantic.SecretStr` so
they do not appear in logs or tracebacks. ``redis_url`` is a plain string, so it
must stay credential-free: a password inside it would be rendered in ``repr``.
A deployment that needs Redis authentication has to revisit this contract first.

The database is configured either as a complete ``DATABASE_URL`` override or as
the individual ``POSTGRES_*`` parts. :func:`sqlalchemy_url` turns whichever form
is present into one :class:`sqlalchemy.engine.URL` for SQLAlchemy and Alembic.
"""

import functools

import sqlalchemy.engine
from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration for one LMI process, built from environment variables."""

    database_url: SecretStr | None = None
    redis_url: str = "redis://localhost:6379/0"
    api_football_key: SecretStr | None = None
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str | None = None
    postgres_password: SecretStr | None = None
    postgres_db: str | None = None


@functools.lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings object, built on first use."""
    return Settings()


def sqlalchemy_url(settings: Settings) -> sqlalchemy.engine.URL:
    """Return the SQLAlchemy URL of the application database.

    ``DATABASE_URL`` is an explicit override: when it is set it wins, even if
    the ``POSTGRES_*`` parts are present too. Otherwise the URL is assembled
    from ``POSTGRES_USER``, ``POSTGRES_PASSWORD``, ``POSTGRES_HOST``,
    ``POSTGRES_PORT`` and ``POSTGRES_DB``.

    The URL is built with :func:`sqlalchemy.engine.URL.create` rather than by
    joining strings, because the constructor percent-encodes every part. A
    password that contains ``@``, ``:``, ``/``, ``?``, ``#`` or ``%`` is
    therefore stored unchanged and survives the round trip through the rendered
    URL.

    Raises:
        ValueError: no ``DATABASE_URL`` is set and one of ``POSTGRES_USER``,
            ``POSTGRES_PASSWORD`` or ``POSTGRES_DB`` is missing. The message
            names the missing environment variables and never a value, so it is
            safe to log.
    """
    if settings.database_url is not None:
        return sqlalchemy.engine.make_url(settings.database_url.get_secret_value())

    user = settings.postgres_user
    password = settings.postgres_password
    database = settings.postgres_db
    if user is None or password is None or database is None:
        missing = [
            name
            for name, value in (
                ("POSTGRES_USER", user),
                ("POSTGRES_PASSWORD", password),
                ("POSTGRES_DB", database),
            )
            if value is None
        ]
        raise ValueError(
            "incomplete database configuration, missing "
            + ", ".join(missing)
            + " (set DATABASE_URL to provide a complete URL instead)"
        )

    return sqlalchemy.engine.URL.create(
        "postgresql+psycopg",
        username=user,
        password=password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=database,
    )
