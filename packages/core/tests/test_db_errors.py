"""The transient-versus-deterministic rule for database failures."""

import pytest
from lmi_core.db.errors import is_transient_db_error, sqlstate_of
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError


class _DriverError(Exception):
    """A stand-in for a psycopg error: the ``sqlstate`` attribute and the text."""

    def __init__(self, sqlstate: str | None, message: str = "driver message") -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def _operational(sqlstate: str | None, message: str = "driver message") -> OperationalError:
    return OperationalError("SELECT 1", {}, _DriverError(sqlstate, message))


@pytest.mark.parametrize(
    "sqlstate",
    [None, "08000", "08001", "08006", "57P01", "57P02", "57P03", "53300", "40001", "40P01"],
)
def test_a_connection_or_server_state_failure_is_transient(sqlstate: str | None) -> None:
    assert is_transient_db_error(_operational(sqlstate)) is True


@pytest.mark.parametrize("sqlstate", ["28P01", "28000", "3D000", "42601", "42P01"])
def test_an_authentication_or_statement_failure_is_deterministic(sqlstate: str) -> None:
    assert is_transient_db_error(_operational(sqlstate)) is False


def test_the_rule_reads_every_wrapped_driver_error() -> None:
    assert is_transient_db_error(InterfaceError("SELECT 1", {}, _DriverError(None))) is True
    assert is_transient_db_error(IntegrityError("INSERT", {}, _DriverError("23505"))) is False


def test_a_socket_failure_is_transient_and_other_errors_are_not() -> None:
    assert is_transient_db_error(ConnectionRefusedError()) is True
    assert is_transient_db_error(ValueError("bad input")) is False


def test_sqlstate_reads_the_wrapped_and_the_bare_driver_error() -> None:
    assert sqlstate_of(_operational("28P01")) == "28P01"
    assert sqlstate_of(_DriverError("40P01")) == "40P01"
    assert sqlstate_of(_operational("")) is None
    assert sqlstate_of(ValueError()) is None


# The texts below are what psycopg 3 reported against a live PostgreSQL 18, with
# the host and the names replaced; every one of them had ``sqlstate=None``.
@pytest.mark.parametrize(
    "message",
    [
        'connection failed: connection to server at "127.0.0.1", port 5432 failed: '
        'FATAL:  password authentication failed for user "probe"',
        'connection failed: connection to server at "127.0.0.1", port 5432 failed: '
        'FATAL:  database "nope" does not exist',
        'connection failed: FATAL:  role "ghost" does not exist',
        'connection failed: FATAL:  no pg_hba.conf entry for host "10.0.0.1"',
    ],
)
def test_a_connect_failure_with_a_deterministic_cause_is_not_transient(message: str) -> None:
    assert is_transient_db_error(_operational(None, message)) is False


@pytest.mark.parametrize(
    "message",
    [
        'connection failed: connection to server at "127.0.0.1", port 5433 failed: '
        "Connection refused\n\tIs the server running on that host and accepting TCP/IP "
        "connections?",
        "connection failed: timeout expired",
        "server closed the connection unexpectedly",
    ],
)
def test_a_connect_failure_without_a_deterministic_cause_is_transient(message: str) -> None:
    assert is_transient_db_error(_operational(None, message)) is True
