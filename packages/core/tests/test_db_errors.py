"""The transient-versus-deterministic rule for database failures."""

import pytest
from lmi_core.db.errors import is_transient_db_error, sqlstate_of
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError


class _DriverError(Exception):
    """A stand-in for a psycopg error: only the ``sqlstate`` attribute matters."""

    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("driver message")
        self.sqlstate = sqlstate


def _operational(sqlstate: str | None) -> OperationalError:
    return OperationalError("SELECT 1", {}, _DriverError(sqlstate))


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
