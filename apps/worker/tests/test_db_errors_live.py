"""The transient rule against a real PostgreSQL connect, not a stand-in error.

libpq reports connect-time failures without a SQLSTATE, so only a live server
shows what the driver really raises. The test database of `make
test-integration` is the server; the wrong password and the unknown database
are fixed placeholder values, never a configured secret.
"""

import os
import socket

import pytest
from conftest import MigratedDatabase
from lmi_core.db.errors import is_transient_db_error
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

WRONG_PASSWORD = "not-the-password"
UNKNOWN_DATABASE = "lmi_no_such_database"


def _url(*, password: str, database: str, port: int) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=password,
        host=os.environ["POSTGRES_HOST"],
        port=port,
        database=database,
    )


def _connect_error(url: URL) -> OperationalError:
    engine = create_engine(url)
    try:
        with pytest.raises(OperationalError) as caught:
            engine.connect()
    finally:
        engine.dispose()
    return caught.value


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.mark.integration
def test_a_wrong_password_on_a_live_server_is_not_transient(
    migrated_engine: MigratedDatabase,
) -> None:
    del migrated_engine  # the fixture proves the server is up and configured
    error = _connect_error(
        _url(
            password=WRONG_PASSWORD,
            database=os.environ["POSTGRES_DB"],
            port=int(os.environ["POSTGRES_PORT"]),
        )
    )
    assert is_transient_db_error(error) is False


@pytest.mark.integration
def test_an_unknown_database_on_a_live_server_is_not_transient(
    migrated_engine: MigratedDatabase,
) -> None:
    del migrated_engine
    error = _connect_error(
        _url(
            password=os.environ["POSTGRES_PASSWORD"],
            database=UNKNOWN_DATABASE,
            port=int(os.environ["POSTGRES_PORT"]),
        )
    )
    assert is_transient_db_error(error) is False


@pytest.mark.integration
def test_a_refused_connection_is_transient() -> None:
    error = _connect_error(
        _url(password=WRONG_PASSWORD, database=UNKNOWN_DATABASE, port=_free_port())
    )
    assert is_transient_db_error(error) is True
