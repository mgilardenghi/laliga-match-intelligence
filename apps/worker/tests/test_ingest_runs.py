"""Unit tests for the run lock that need no database.

The run lock is a session-level advisory lock on a dedicated pooled connection,
so the release is the step that decides whether a later job of the same process
can take the lock again. These tests pin three properties over a fake connection:
a released lock leaves the connection in the pool, a job that never got the lock
releases nothing, and a failed release discards the connection instead of putting
the lock back into the pool. The last test also pins the error priority: the
exception of the ``with`` block is the one the caller sees, because that is the
one it can act on.
"""

from __future__ import annotations

import logging
from typing import cast

import pytest
from lmi_worker.ingest.plan import Job
from lmi_worker.ingest.runs import run_lock
from lmi_worker.seasons import SeasonCode
from sqlalchemy.engine import Engine

JOB = Job(source="football_data", season=SeasonCode.parse("2025-26"))
"""The job whose lock every test takes."""


class _FakeResult:
    """What ``execute`` returns for the two statements the lock runs."""

    def __init__(self, acquired: bool) -> None:
        self._acquired = acquired

    def scalar_one(self) -> bool:
        return self._acquired


class _FakeConnection:
    """A connection stand-in that records every call the lock makes."""

    def __init__(self, *, acquired: bool = True, release_error: Exception | None = None) -> None:
        self.acquired = acquired
        self.release_error = release_error
        self.statements: list[str] = []
        self.invalidated = False
        self.closed = False

    def execute(self, statement: object, parameters: object = None) -> _FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        if "pg_advisory_unlock" in sql and self.release_error is not None:
            raise self.release_error
        return _FakeResult(self.acquired)

    def commit(self) -> None:
        pass

    def invalidate(self) -> None:
        self.invalidated = True

    def close(self) -> None:
        self.closed = True


class _FakeEngine:
    """An engine stand-in that hands out one prepared connection."""

    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def connect(self) -> _FakeConnection:
        return self.connection


def _engine(connection: _FakeConnection) -> Engine:
    """Return the fake engine, typed as the engine the lock declares."""
    return cast(Engine, _FakeEngine(connection))


def test_a_released_lock_closes_the_connection_and_keeps_it_pooled() -> None:
    connection = _FakeConnection()

    with run_lock(_engine(connection), JOB) as acquired:
        assert acquired is True
        assert connection.closed is False

    assert connection.closed is True
    assert connection.invalidated is False
    assert any("pg_try_advisory_lock" in sql for sql in connection.statements)
    assert any("pg_advisory_unlock" in sql for sql in connection.statements)


def test_a_job_that_did_not_get_the_lock_releases_nothing() -> None:
    connection = _FakeConnection(acquired=False)

    with run_lock(_engine(connection), JOB) as acquired:
        assert acquired is False

    assert connection.closed is True
    assert connection.invalidated is False
    assert not any("pg_advisory_unlock" in sql for sql in connection.statements)


def test_a_failed_release_discards_the_connection_and_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failure = RuntimeError("the server went away")
    connection = _FakeConnection(release_error=failure)
    engine = _engine(connection)
    caplog.set_level(logging.WARNING, logger="lmi_worker.ingest.runs")

    with pytest.raises(RuntimeError, match="the server went away"), run_lock(engine, JOB):
        pass

    assert connection.invalidated is True
    assert connection.closed is True
    assert [record.getMessage() for record in caplog.records] == [
        "the run lock of football_data 2025-26 could not be released (RuntimeError); "
        "the connection was discarded"
    ]
    assert "postgresql" not in caplog.text


def test_a_failed_release_does_not_mask_the_error_of_the_block() -> None:
    connection = _FakeConnection(release_error=RuntimeError("the server went away"))
    engine = _engine(connection)

    with pytest.raises(ValueError, match="the load failed"), run_lock(engine, JOB):
        raise ValueError("the load failed")

    assert connection.invalidated is True
    assert connection.closed is True
