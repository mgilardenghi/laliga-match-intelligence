"""Behavior tests for the worker command line entry point without a database.

The tests cover the exit-code paths that must work before any connection exists:
a successful ``--help``, a malformed command line, a rejected plan, and a missing
database configuration for both commands. Every command that needs configuration
runs with the ``POSTGRES_*`` variables removed, so the configuration error is the
one a fresh shell sees.

One test points the configuration at a closed port: ``ingest`` checks the database
once before the first job, so an unreachable server is one line on stderr and exit
code 1 instead of a traceback per job, and no file is downloaded.

One test pins the log contract of a finished ingestion over a faked runner: one
line per job plus the summary, which is what the acceptance run reads. The engine
and the runner are replaced there, so no connection is opened and no file is
downloaded.

``get_settings`` is cached, so the tests clear the cache around the environment
change; the subprocess test starts from a clean process and does not need it.
"""

import logging
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from datetime import date
from uuid import UUID

import pytest
from ingest_support import FakeFetcher
from lmi_core.config import get_settings
from lmi_worker import cli
from lmi_worker.cli import main
from lmi_worker.ingest.pipeline import JobResult
from lmi_worker.ingest.plan import Job
from lmi_worker.seasons import SeasonCode

TODAY = date(2026, 11, 14)
"""A date inside the 2026-27 season, the newest seeded season."""

_CONFIGURATION_VARIABLES = (
    "DATABASE_URL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)

_SUCCEEDED = JobResult(
    job=Job(source="football_data", season=SeasonCode.parse("2005-06")),
    status="succeeded",
    run_id=UUID(int=1),
    rows_read=380,
    rows_upserted=380,
)
"""A job that wrote a complete season."""

_FAILED = JobResult(
    job=Job(source="football_data", season=SeasonCode.parse("2006-07")),
    status="failed",
    run_id=UUID(int=2),
    rows_read=380,
    rows_rejected=1,
    error="VintageError: an inconsistent full-time result",
)
"""A job whose file was rejected as a whole."""

_LOCKED = JobResult(
    job=Job(source="openfootball", season=SeasonCode.parse("2026-27")),
    status="locked",
    run_id=None,
)
"""A job another process was already running, so it has no run row."""


class _ConnectionStand:
    """A stand-in connection, so the database preflight needs no server."""

    def __enter__(self) -> _ConnectionStand:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def execute(self, statement: object) -> None:
        pass


class _EngineStand:
    """A stand-in engine, so a faked runner needs no database connection."""

    def __init__(self) -> None:
        self.disposed = False

    def connect(self) -> _ConnectionStand:
        """Answer the preflight statement the command runs before the first job."""
        return _ConnectionStand()

    def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove every database setting for the duration of one test."""
    for name in _CONFIGURATION_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


_UNREACHABLE_DATABASE = {
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "1",
    "POSTGRES_USER": "placeholder",
    "POSTGRES_PASSWORD": "placeholder",
    "POSTGRES_DB": "placeholder",
}
"""Settings that point at a port nothing listens on, so a connection fails at once."""


@pytest.fixture
def unreachable_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the database settings at a closed port for the duration of one test."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name, value in _UNREACHABLE_DATABASE.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _fake_runner(monkeypatch: pytest.MonkeyPatch, results: Sequence[JobResult]) -> _EngineStand:
    """Replace the engine and the job runner, so only the command line is exercised."""
    engine = _EngineStand()

    def build_engine(settings: object) -> _EngineStand:
        return engine

    def run_jobs(engine: object, jobs: object, fetch: object, pause: object) -> list[JobResult]:
        return list(results)

    monkeypatch.setattr(cli, "make_engine", build_engine)
    monkeypatch.setattr(cli, "run_jobs", run_jobs)
    return engine


def test_help_exits_with_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0


def test_two_exclusive_season_flags_exit_with_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["ingest", "--season", "2026-27", "--backfill"])

    assert excinfo.value.code == 2


def test_ingest_without_configuration_exits_two_and_fetches_nothing(
    unconfigured: None, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher = FakeFetcher({})

    code = main(["ingest", "--season", "2026-27"], today=TODAY, fetcher=fetcher)

    assert code == 2
    assert "incomplete database configuration" in capsys.readouterr().err
    assert fetcher.requested == []


def test_the_default_season_after_the_seeded_range_exits_two_without_a_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse_engine(settings: object) -> object:
        raise AssertionError("a rejected plan must not build the database engine")

    monkeypatch.setattr(cli, "make_engine", refuse_engine)
    fetcher = FakeFetcher({})

    code = main(["ingest"], today=date(2027, 7, 1), fetcher=fetcher)

    assert code == 2
    assert fetcher.requested == []
    assert "2027-28" in capsys.readouterr().err


def test_ingest_against_an_unreachable_database_exits_one_without_a_traceback(
    unreachable_database: None, capsys: pytest.CaptureFixture[str]
) -> None:
    fetcher = FakeFetcher({})

    code = main(["ingest", "--season", "2026-27"], today=TODAY, fetcher=fetcher)

    assert code == 1
    error = capsys.readouterr().err
    assert error.strip() == "ingest: cannot reach the database"
    assert "Traceback" not in error
    assert fetcher.requested == []


def test_the_command_line_logs_one_line_per_job_and_the_summary(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    engine = _fake_runner(monkeypatch, [_SUCCEEDED, _FAILED, _LOCKED])
    caplog.set_level(logging.INFO, logger="lmi_worker.cli")

    code = main(["ingest"], today=TODAY, fetcher=FakeFetcher({}))

    assert code == 1
    assert engine.disposed is True
    assert [record.getMessage() for record in caplog.records] == [
        "football_data 2005-06 succeeded read=380 upserted=380 removed=0 rejected=0 "
        "run=00000000-0000-0000-0000-000000000001",
        "football_data 2006-07 failed read=380 upserted=0 removed=0 rejected=1 "
        "run=00000000-0000-0000-0000-000000000002",
        "openfootball 2026-27 locked read=0 upserted=0 removed=0 rejected=0 run=none",
        "ingest: 1 succeeded, 2 failed",
    ]


def test_a_run_where_every_job_succeeds_exits_with_zero(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    other_season = JobResult(
        job=Job(source="openfootball", season=SeasonCode.parse("2026-27")),
        status="succeeded",
        run_id=UUID(int=3),
        rows_read=380,
        rows_upserted=380,
    )
    _fake_runner(monkeypatch, [_SUCCEEDED, other_season])
    caplog.set_level(logging.INFO, logger="lmi_worker.cli")

    code = main(["ingest"], today=TODAY, fetcher=FakeFetcher({}))

    assert code == 0
    assert caplog.records[-1].getMessage() == "ingest: 2 succeeded, 0 failed"


def test_status_without_configuration_exits_two_in_a_subprocess() -> None:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("POSTGRES_") and name.upper() != "DATABASE_URL"
    }

    result = subprocess.run(
        [sys.executable, "-m", "lmi_worker", "status"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 2
    assert "incomplete database configuration" in result.stderr
